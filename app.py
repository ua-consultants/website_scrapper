import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from PIL import Image
import io
import hashlib
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches
import time
import re
from typing import List, Dict, Set, Tuple
import concurrent.futures

# Page config
st.set_page_config(page_title="E-Commerce to PPT", page_icon="🛋️", layout="wide")

# Constants optimized for FREE TIER (1GB RAM limit)
PRODUCT_CONTAINERS = {'product', 'item', 'card', 'collection', 'gallery', 'grid', 'listing'}
IGNORE_CONTAINERS = {'header', 'footer', 'nav', 'menu', 'svg', 'button', 'icon', 'logo'}
REJECT_KEYWORDS = {'logo', 'icon', 'sprite', 'badge', 'arrow', 'cart', 'heart', 'star',
                   'payment', 'visa', 'mastercard', 'banner', 'slider', 'ad', 'thumb'}
ACCEPT_KEYWORDS = {'product', 'item', 'furniture', 'sofa', 'chair', 'table', 'bed', 
                   'cabinet', 'desk', 'couch', 'dresser', 'shelf'}
MIN_WIDTH = 500  # Reduced for faster processing
MIN_HEIGHT = 500
MIN_SQUARE = 350
MAX_IMAGES = 30  # CRITICAL: Reduced from 100 to prevent memory issues
MAX_PAGES = 3    # CRITICAL: Reduced from 10 to stay within limits
TIMEOUT = 8      # Reduced timeout
MAX_FILE_SIZE = 800 * 1024  # 800KB max per image to prevent memory bloat

class ImageScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.domain = urlparse(base_url).netloc
        self.seen_hashes: Set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
    
    def analyze_site(self, status_container) -> str:
        """Detect if site needs JS rendering - FREE TIER: Skip browser automation"""
        try:
            status_container.write("🔍 Analyzing site...")
            response = self.session.get(self.base_url, timeout=TIMEOUT)
            response.raise_for_status()
            
            # FREE TIER OPTIMIZATION: Always use requests (no Selenium)
            # Selenium uses too much memory on free tier
            status_container.write("📄 Using fast scraping (optimized for free tier)")
            return "requests"
        except Exception as e:
            status_container.write(f"⚠️ Analysis failed: {str(e)[:80]}")
            return "requests"
    
    def crawl_pages(self, status_container) -> List[str]:
        """Discover product pages - optimized for free tier"""
        pages = [self.base_url]
        status_container.write(f"🌐 Crawling {self.base_url}...")
        
        try:
            response = self.session.get(self.base_url, timeout=TIMEOUT)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(self.base_url, href)
                
                if self.domain in full_url and any(kw in full_url.lower() for kw in 
                    ['product', 'collection', 'shop', 'furniture', 'category']):
                    pages.append(full_url)
                    if len(pages) >= MAX_PAGES:
                        break
        except Exception as e:
            status_container.write(f"⚠️ Crawl error: {str(e)[:80]}")
        
        pages = list(set(pages))[:MAX_PAGES]
        status_container.write(f"✅ Found {len(pages)} pages to scrape")
        return pages
    
    def extract_candidate_images(self, url: str) -> List[Dict]:
        """Extract all image candidates from a page"""
        candidates = []
        
        try:
            response = self.session.get(url, timeout=TIMEOUT)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if not src:
                    continue
                
                full_url = urljoin(url, src)
                
                # Check DOM context
                parent_classes = ' '.join(img.parent.get('class', [])).lower() if img.parent else ''
                parent_id = img.parent.get('id', '').lower() if img.parent else ''
                context = parent_classes + ' ' + parent_id
                
                candidates.append({
                    'url': full_url,
                    'context': context,
                    'alt': img.get('alt', '').lower()
                })
                
                # Memory protection: limit candidates per page
                if len(candidates) >= 50:
                    break
        except Exception:
            pass
        
        return candidates
    
    def filter_product_images(self, candidates: List[Dict]) -> List[str]:
        """Apply intelligent filtering to identify product images"""
        filtered = []
        
        for candidate in candidates:
            url = candidate['url']
            context = candidate['context'].lower()
            
            # Rule 1: DOM Context
            has_product_context = any(kw in context for kw in PRODUCT_CONTAINERS)
            has_ignore_context = any(kw in context for kw in IGNORE_CONTAINERS)
            
            if has_ignore_context:
                continue
            
            # Rule 2: URL/Filename filtering
            url_lower = url.lower()
            has_reject = any(kw in url_lower for kw in REJECT_KEYWORDS)
            has_accept = any(kw in url_lower for kw in ACCEPT_KEYWORDS)
            
            if has_reject and not has_accept:
                continue
            
            # Rule 3: Must be real image format
            if not re.search(r'\.(jpg|jpeg|png|webp)', url_lower):
                continue
            
            # Prioritize images with product context or accept keywords
            if has_product_context or has_accept:
                filtered.append(url)
        
        return list(set(filtered))
    
    def download_and_validate_images(self, urls: List[str], progress_bar, status_container) -> List[Tuple[str, bytes]]:
        """Download and validate images - MEMORY OPTIMIZED"""
        valid_images = []
        
        def process_image(url):
            try:
                # Stream download with size check
                response = self.session.get(url, timeout=TIMEOUT, stream=True)
                response.raise_for_status()
                
                # Check content length before downloading
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > MAX_FILE_SIZE:
                    return None
                
                # Read with size limit
                img_bytes = b''
                for chunk in response.iter_content(chunk_size=8192):
                    img_bytes += chunk
                    if len(img_bytes) > MAX_FILE_SIZE:
                        return None
                
                # Check hash for duplicates
                img_hash = hashlib.sha256(img_bytes).hexdigest()
                if img_hash in self.seen_hashes:
                    return None
                
                # Validate dimensions
                try:
                    img = Image.open(io.BytesIO(img_bytes))
                    width, height = img.size
                    
                    # Dimension checks
                    if width < MIN_WIDTH or height < MIN_HEIGHT:
                        return None
                    
                    if width == height and width < MIN_SQUARE:
                        return None
                    
                    # MEMORY OPTIMIZATION: Resize large images
                    max_dimension = 1920
                    if width > max_dimension or height > max_dimension:
                        ratio = min(max_dimension/width, max_dimension/height)
                        new_size = (int(width * ratio), int(height * ratio))
                        img = img.resize(new_size, Image.Resampling.LANCZOS)
                    
                    # Convert to RGB if needed
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    # Compress to save memory
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=75, optimize=True)
                    final_bytes = output.getvalue()
                    
                    self.seen_hashes.add(img_hash)
                    return (url, final_bytes)
                
                except Exception:
                    return None
                
            except Exception:
                return None
        
        total = min(len(urls), MAX_IMAGES)
        processed = 0
        
        # Process with limited concurrency (memory protection)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(process_image, url): url for url in urls[:MAX_IMAGES]}
            
            for future in concurrent.futures.as_completed(futures):
                processed += 1
                progress_bar.progress(processed / total)
                result = future.result()
                if result:
                    valid_images.append(result)
                    status_container.write(f"✅ Image {len(valid_images)}/{MAX_IMAGES}")
                    
                    if len(valid_images) >= MAX_IMAGES:
                        # Cancel remaining futures
                        for f in futures:
                            f.cancel()
                        break
        
        return valid_images

@st.cache_data(ttl=3600)
def generate_ppt(images: List[Tuple[str, bytes]], domain: str) -> bytes:
    """Generate PowerPoint with one image per slide"""
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625)  # 16:9 ratio
    
    blank_layout = prs.slide_layouts[6]
    
    for url, img_bytes in images:
        slide = prs.slides.add_slide(blank_layout)
        
        # Set white background
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = (255, 255, 255)
        
        # Load image
        img = Image.open(io.BytesIO(img_bytes))
        img_width, img_height = img.size
        
        # Calculate scaling
        slide_width = prs.slide_width.inches
        slide_height = prs.slide_height.inches
        
        img_ratio = img_width / img_height
        slide_ratio = slide_width / slide_height
        
        if img_ratio > slide_ratio:
            width = Inches(slide_width * 0.9)
            height = Inches((slide_width * 0.9) / img_ratio)
        else:
            height = Inches(slide_height * 0.9)
            width = Inches((slide_height * 0.9) * img_ratio)
        
        # Center position
        left = Inches((slide_width - width.inches) / 2)
        top = Inches((slide_height - height.inches) / 2)
        
        # Add image
        img_stream = io.BytesIO(img_bytes)
        slide.shapes.add_picture(img_stream, left, top, width, height)
    
    # Save to bytes
    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()

def main():
    st.title("🛋️ E-Commerce Product Image to PowerPoint")
    st.markdown("**Optimized for Streamlit Free Tier** • Extract product images from furniture websites")
    
    # Resource warning
    with st.expander("ℹ️ Free Tier Optimizations", expanded=False):
        st.info(f"""
        **Memory-Optimized Settings:**
        - Maximum {MAX_IMAGES} images per PPT
        - Maximum {MAX_PAGES} pages scraped
        - Images resized to 1920px max
        - Fast scraping only (no browser automation)
        
        These limits ensure the app runs smoothly on Streamlit's free tier (1GB RAM).
        """)
    
    url = st.text_input("🌐 Enter E-Commerce Website URL:", placeholder="https://example-furniture-store.com")
    
    if st.button("🚀 Generate PPT", type="primary"):
        if not url:
            st.error("Please enter a valid URL")
            return
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        progress_bar = st.progress(0)
        status_container = st.status("Starting scraping process...", expanded=True)
        
        try:
            scraper = ImageScraper(url)
            
            # Step 1: Analyze site
            progress_bar.progress(10)
            method = scraper.analyze_site(status_container)
            
            # Step 2: Crawl pages
            progress_bar.progress(20)
            pages = scraper.crawl_pages(status_container)
            
            # Step 3: Extract and filter images
            progress_bar.progress(30)
            all_candidates = []
            
            for i, page in enumerate(pages):
                status_container.write(f"📄 Scraping page {i+1}/{len(pages)}")
                candidates = scraper.extract_candidate_images(page)
                all_candidates.extend(candidates)
                progress_bar.progress(30 + (20 * (i+1) / len(pages)))
            
            status_container.write(f"🔍 Found {len(all_candidates)} candidate images")
            
            # Step 4: Filter product images
            progress_bar.progress(50)
            filtered_urls = scraper.filter_product_images(all_candidates)
            status_container.write(f"✅ Filtered to {len(filtered_urls)} product images")
            
            if not filtered_urls:
                status_container.update(label="❌ No product images found", state="error")
                st.error("No product images found. Try a different URL with visible product images.")
                return
            
            # Step 5: Download and validate
            progress_bar.progress(60)
            status_container.write(f"⬇️ Downloading up to {MAX_IMAGES} images...")
            valid_images = scraper.download_and_validate_images(
                filtered_urls, 
                progress_bar, 
                status_container
            )
            
            if not valid_images:
                status_container.update(label="❌ No valid images downloaded", state="error")
                st.error("All images failed validation. The site may have anti-scraping measures.")
                return
            
            status_container.write(f"✅ Successfully downloaded {len(valid_images)} images")
            
            # Step 6: Generate PPT
            progress_bar.progress(90)
            status_container.write("📊 Generating PowerPoint...")
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{scraper.domain.replace('.', '_')}_products_{timestamp}.pptx"
            
            ppt_bytes = generate_ppt(valid_images, scraper.domain)
            
            progress_bar.progress(100)
            status_container.update(label="✅ Complete!", state="complete")
            
            # Display results
            st.success(f"🎉 Successfully created PowerPoint with {len(valid_images)} product images!")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Images", len(valid_images))
            with col2:
                st.metric("Pages Scraped", len(pages))
            with col3:
                st.metric("File Size", f"{len(ppt_bytes) / 1024 / 1024:.1f} MB")
            
            # Download button
            st.download_button(
                label="📥 Download PowerPoint",
                data=ppt_bytes,
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                type="primary"
            )
            
            # Preview
            st.subheader("🖼️ Preview (First 5 Images)")
            cols = st.columns(5)
            for i, (url, img_bytes) in enumerate(valid_images[:5]):
                with cols[i]:
                    st.image(img_bytes, use_container_width=True, caption=f"Image {i+1}")
        
        except Exception as e:
            status_container.update(label=f"❌ Error: {str(e)}", state="error")
            st.error(f"Fatal error: {str(e)}")
            st.exception(e)

if __name__ == "__main__":
    main()
