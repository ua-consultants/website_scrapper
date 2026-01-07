import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from PIL import Image
import io
import hashlib
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches, Pt
import time
import re
from typing import List, Dict, Set, Tuple
import concurrent.futures
from pathlib import Path

# Page config
st.set_page_config(page_title="E-Commerce to PPT", page_icon="🛋️", layout="wide")

# Constants
PRODUCT_CONTAINERS = {'product', 'item', 'card', 'collection', 'gallery', 'grid', 'listing'}
IGNORE_CONTAINERS = {'header', 'footer', 'nav', 'menu', 'svg', 'button', 'icon', 'logo'}
REJECT_KEYWORDS = {'logo', 'icon', 'sprite', 'badge', 'arrow', 'cart', 'heart', 'star',
                   'payment', 'visa', 'mastercard', 'banner', 'slider', 'ad', 'thumb'}
ACCEPT_KEYWORDS = {'product', 'item', 'furniture', 'sofa', 'chair', 'table', 'bed', 
                   'cabinet', 'desk', 'couch', 'dresser', 'shelf'}
MIN_WIDTH = 600
MIN_HEIGHT = 600
MIN_SQUARE = 400
MAX_IMAGES = 100
TIMEOUT = 10

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
            'Referer': base_url
        })
    
    def analyze_site(self, status_container) -> str:
        """Detect if site is JS-heavy or static"""
        try:
            status_container.write("🔍 Analyzing site architecture...")
            response = self.session.get(self.base_url, timeout=TIMEOUT)
            response.raise_for_status()
            
            html = response.text.lower()
            js_indicators = html.count('<script') + html.count('react') + html.count('vue') + html.count('angular')
            static_indicators = html.count('<img') + html.count('srcset')
            
            if js_indicators > 10 and static_indicators < 5:
                status_container.write("⚡ Detected JS-heavy site - using browser automation")
                return "selenium"
            else:
                status_container.write("📄 Detected static site - using fast scraping")
                return "requests"
        except Exception as e:
            status_container.write(f"⚠️ Analysis failed: {str(e)[:100]} - defaulting to requests")
            return "requests"
    
    def crawl_pages(self, method: str, status_container) -> List[str]:
        """Discover product pages"""
        pages = [self.base_url]
        status_container.write(f"🌐 Crawling {self.base_url}...")
        
        try:
            if method == "selenium":
                pages.extend(self._crawl_with_playwright(status_container))
            else:
                pages.extend(self._crawl_with_requests(status_container))
        except Exception as e:
            status_container.write(f"⚠️ Crawl error: {str(e)[:100]}")
        
        pages = list(set(pages))[:10]  # Limit to 10 pages
        status_container.write(f"✅ Found {len(pages)} pages to scrape")
        return pages
    
    def _crawl_with_requests(self, status_container) -> List[str]:
        """Crawl using requests"""
        urls = []
        try:
            response = self.session.get(self.base_url, timeout=TIMEOUT)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(self.base_url, href)
                
                if self.domain in full_url and any(kw in full_url.lower() for kw in 
                    ['product', 'collection', 'shop', 'furniture', 'category']):
                    urls.append(full_url)
                    if len(urls) >= 10:
                        break
        except Exception as e:
            status_container.write(f"⚠️ Requests crawl failed: {str(e)[:50]}")
        
        return urls
    
    def _crawl_with_playwright(self, status_container) -> List[str]:
        """Crawl using Selenium for JS sites"""
        urls = []
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(30)
            driver.get(self.base_url)
            
            # Scroll to load lazy content
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, window.innerHeight)")
                time.sleep(0.5)
            
            # Extract links
            links = driver.execute_script("""
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(href => href.includes('product') || href.includes('shop') || href.includes('collection'));
            """)
            
            urls = [url for url in links if self.domain in url][:10]
            driver.quit()
        except ImportError:
            status_container.write("⚠️ Selenium not available - using requests fallback")
        except Exception as e:
            status_container.write(f"⚠️ Selenium error: {str(e)[:50]}")
        
        return urls
    
    def extract_candidate_images(self, url: str, method: str) -> List[Dict]:
        """Extract all image candidates from a page"""
        candidates = []
        
        try:
            if method == "selenium":
                candidates = self._extract_with_playwright(url)
            else:
                candidates = self._extract_with_requests(url)
        except Exception as e:
            st.warning(f"Extract failed for {url[:50]}: {str(e)[:50]}")
        
        return candidates
    
    def _extract_with_requests(self, url: str) -> List[Dict]:
        """Extract images using requests"""
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
        except Exception as e:
            pass
        
        return candidates
    
    def _extract_with_playwright(self, url: str) -> List[Dict]:
        """Extract images using Selenium"""
        candidates = []
        
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(30)
            driver.get(url)
            
            # Scroll to trigger lazy loading
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, window.innerHeight)")
                time.sleep(0.3)
            
            # Extract image data
            images = driver.execute_script("""
                return Array.from(document.querySelectorAll('img')).map(img => ({
                    url: img.src || img.dataset.src || img.dataset.lazySrc || '',
                    context: (img.parentElement?.className || '') + ' ' + (img.parentElement?.id || ''),
                    alt: img.alt || ''
                }));
            """)
            
            candidates = [img for img in images if img['url']]
            driver.quit()
        except Exception as e:
            pass
        
        return candidates
    
    def filter_product_images(self, candidates: List[Dict]) -> List[str]:
        """Apply intelligent filtering to identify product images"""
        filtered = []
        
        for candidate in candidates:
            url = candidate['url']
            context = candidate['context'].lower()
            alt = candidate['alt']
            
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
        """Download and validate images in parallel"""
        valid_images = []
        
        def process_image(url):
            try:
                response = self.session.get(url, timeout=TIMEOUT, stream=True)
                response.raise_for_status()
                
                img_bytes = response.content
                
                # Check hash for duplicates
                img_hash = hashlib.sha256(img_bytes).hexdigest()
                if img_hash in self.seen_hashes:
                    return None
                
                # Validate dimensions
                try:
                    img = Image.open(io.BytesIO(img_bytes))
                    width, height = img.size
                    
                    # Rule 3: Dimension checks
                    if width < MIN_WIDTH or height < MIN_HEIGHT:
                        return None
                    
                    if width == height and width < MIN_SQUARE:
                        return None
                    
                    # Convert to RGB if needed
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    # Save to bytes
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=85)
                    final_bytes = output.getvalue()
                    
                    self.seen_hashes.add(img_hash)
                    return (url, final_bytes)
                
                except Exception:
                    return None
                
            except Exception:
                return None
        
        total = len(urls)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(process_image, url): url for url in urls}
            
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                progress_bar.progress((i + 1) / total)
                result = future.result()
                if result:
                    valid_images.append(result)
                    status_container.write(f"✅ Downloaded: {result[0][:60]}...")
                    
                    if len(valid_images) >= MAX_IMAGES:
                        break
        
        return valid_images

def generate_ppt(images: List[Tuple[str, bytes]], domain: str) -> bytes:
    """Generate PowerPoint with one image per slide"""
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625)  # 16:9 ratio
    
    blank_layout = prs.slide_layouts[6]  # Blank layout
    
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
        
        # Calculate scaling to fit slide
        slide_width = prs.slide_width.inches
        slide_height = prs.slide_height.inches
        
        img_ratio = img_width / img_height
        slide_ratio = slide_width / slide_height
        
        if img_ratio > slide_ratio:
            # Image is wider - fit to width
            width = Inches(slide_width * 0.9)
            height = Inches((slide_width * 0.9) / img_ratio)
        else:
            # Image is taller - fit to height
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
    st.markdown("Extract product images from furniture websites and generate a professional PowerPoint presentation.")
    
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
            pages = scraper.crawl_pages(method, status_container)
            
            # Step 3: Extract and filter images
            progress_bar.progress(30)
            all_candidates = []
            
            for i, page in enumerate(pages):
                status_container.write(f"📄 Scraping page {i+1}/{len(pages)}: {page[:60]}...")
                candidates = scraper.extract_candidate_images(page, method)
                all_candidates.extend(candidates)
                progress_bar.progress(30 + (20 * (i+1) / len(pages)))
            
            status_container.write(f"🔍 Found {len(all_candidates)} candidate images")
            
            # Step 4: Filter product images
            progress_bar.progress(50)
            filtered_urls = scraper.filter_product_images(all_candidates)
            status_container.write(f"✅ Filtered to {len(filtered_urls)} product images")
            
            if not filtered_urls:
                status_container.update(label="❌ No product images found", state="error")
                st.error("No product images found. The site may have strong anti-scraping measures or no products.")
                return
            
            # Step 5: Download and validate
            progress_bar.progress(60)
            status_container.write("⬇️ Downloading and validating images...")
            valid_images = scraper.download_and_validate_images(
                filtered_urls, 
                progress_bar, 
                status_container
            )
            
            if not valid_images:
                status_container.update(label="❌ No valid images found", state="error")
                st.error("All images failed validation (dimensions, duplicates, or download errors)")
                return
            
            status_container.write(f"✅ Successfully validated {len(valid_images)} unique images")
            
            # Step 6: Generate PPT
            progress_bar.progress(90)
            status_container.write("📊 Generating PowerPoint presentation...")
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{scraper.domain.replace('.', '_')}_products_{timestamp}.pptx"
            
            ppt_bytes = generate_ppt(valid_images, scraper.domain)
            
            progress_bar.progress(100)
            status_container.update(label="✅ Complete!", state="complete")
            
            # Display results
            st.success(f"🎉 Successfully created PowerPoint with {len(valid_images)} product images!")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Images Found", len(valid_images))
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
