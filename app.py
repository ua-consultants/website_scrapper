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
import zipfile

# Page config
st.set_page_config(page_title="E-Commerce to PPT", page_icon="🛋️", layout="wide")

# Constants optimized for FREE TIER with batch processing
PRODUCT_CONTAINERS = {'product', 'item', 'card', 'collection', 'gallery', 'grid', 'listing'}
IGNORE_CONTAINERS = {'header', 'footer', 'nav', 'menu', 'svg', 'button', 'icon', 'logo'}
REJECT_KEYWORDS = {'logo', 'icon', 'sprite', 'badge', 'arrow', 'cart', 'heart', 'star',
                   'payment', 'visa', 'mastercard', 'banner', 'slider', 'ad', 'thumb'}
ACCEPT_KEYWORDS = {'product', 'item', 'furniture', 'sofa', 'chair', 'table', 'bed', 
                   'cabinet', 'desk', 'couch', 'dresser', 'shelf'}
MIN_WIDTH = 500
MIN_HEIGHT = 500
MIN_SQUARE = 350
IMAGES_PER_PPT = 50  # Batch size for each PPT file
MAX_TOTAL_IMAGES = 200  # Maximum total images to scrape
MAX_PAGES = 5  # Increased to get more images
TIMEOUT = 8
MAX_FILE_SIZE = 800 * 1024

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
                
                if len(candidates) >= 100:
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
            
            has_product_context = any(kw in context for kw in PRODUCT_CONTAINERS)
            has_ignore_context = any(kw in context for kw in IGNORE_CONTAINERS)
            
            if has_ignore_context:
                continue
            
            url_lower = url.lower()
            has_reject = any(kw in url_lower for kw in REJECT_KEYWORDS)
            has_accept = any(kw in url_lower for kw in ACCEPT_KEYWORDS)
            
            if has_reject and not has_accept:
                continue
            
            if not re.search(r'\.(jpg|jpeg|png|webp)', url_lower):
                continue
            
            if has_product_context or has_accept:
                filtered.append(url)
        
        return list(set(filtered))
    
    def download_and_validate_images(self, urls: List[str], progress_bar, status_container, max_images: int = MAX_TOTAL_IMAGES) -> List[Tuple[str, bytes]]:
        """Download and validate images - MEMORY OPTIMIZED with streaming"""
        valid_images = []
        
        def process_image(url):
            try:
                response = self.session.get(url, timeout=TIMEOUT, stream=True)
                response.raise_for_status()
                
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > MAX_FILE_SIZE:
                    return None
                
                img_bytes = b''
                for chunk in response.iter_content(chunk_size=8192):
                    img_bytes += chunk
                    if len(img_bytes) > MAX_FILE_SIZE:
                        return None
                
                img_hash = hashlib.sha256(img_bytes).hexdigest()
                if img_hash in self.seen_hashes:
                    return None
                
                try:
                    img = Image.open(io.BytesIO(img_bytes))
                    width, height = img.size
                    
                    if width < MIN_WIDTH or height < MIN_HEIGHT:
                        return None
                    
                    if width == height and width < MIN_SQUARE:
                        return None
                    
                    max_dimension = 1920
                    if width > max_dimension or height > max_dimension:
                        ratio = min(max_dimension/width, max_dimension/height)
                        new_size = (int(width * ratio), int(height * ratio))
                        img = img.resize(new_size, Image.Resampling.LANCZOS)
                    
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=75, optimize=True)
                    final_bytes = output.getvalue()
                    
                    self.seen_hashes.add(img_hash)
                    return (url, final_bytes)
                
                except Exception:
                    return None
                
            except Exception:
                return None
        
        total = min(len(urls), max_images)
        processed = 0
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(process_image, url): url for url in urls[:max_images]}
            
            for future in concurrent.futures.as_completed(futures):
                processed += 1
                # Progress from 0.30 to 0.75 (download phase)
                download_progress = 0.30 + (0.45 * processed / total)
                progress_bar.progress(min(download_progress, 0.75))
                result = future.result()
                if result:
                    valid_images.append(result)
                    if len(valid_images) % 10 == 0:
                        status_container.write(f"✅ Downloaded {len(valid_images)} images...")
                    
                    if len(valid_images) >= max_images:
                        for f in futures:
                            f.cancel()
                        break
        
        return valid_images

def generate_single_ppt(images: List[Tuple[str, bytes]], domain: str, batch_num: int, images_per_slide: int = 4) -> bytes:
    """Generate a single PowerPoint with multiple images per slide"""
    from pptx.util import Inches
    from pptx.dml.color import RGBColor
    
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625)
    
    blank_layout = prs.slide_layouts[6]
    
    for batch_start in range(0, len(images), images_per_slide):
        batch = images[batch_start:batch_start + images_per_slide]
        slide = prs.slides.add_slide(blank_layout)
        
        # Set white background with proper RGBColor
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(255, 255, 255)
        
        num_images = len(batch)
        if num_images == 1:
            grid = [(0, 0)]
            cols, rows = 1, 1
        elif num_images == 2:
            grid = [(0, 0), (1, 0)]
            cols, rows = 2, 1
        elif num_images == 3:
            grid = [(0, 0), (1, 0), (0, 1)]
            cols, rows = 2, 2
        else:
            grid = [(0, 0), (1, 0), (0, 1), (1, 1)]
            cols, rows = 2, 2
        
        slide_width = prs.slide_width.inches
        slide_height = prs.slide_height.inches
        
        margin = 0.3
        h_spacing = 0.2
        v_spacing = 0.2
        
        available_width = slide_width - (2 * margin) - ((cols - 1) * h_spacing)
        available_height = slide_height - (2 * margin) - ((rows - 1) * v_spacing)
        
        cell_width = available_width / cols
        cell_height = available_height / rows
        
        for idx, (url, img_bytes) in enumerate(batch):
            col, row = grid[idx]
            
            img = Image.open(io.BytesIO(img_bytes))
            img_width, img_height = img.size
            img_ratio = img_width / img_height
            
            if img_ratio > (cell_width / cell_height):
                width = Inches(cell_width)
                height = Inches(cell_width / img_ratio)
            else:
                height = Inches(cell_height)
                width = Inches(cell_height * img_ratio)
            
            left = Inches(margin + (col * (cell_width + h_spacing)) + (cell_width - width.inches) / 2)
            top = Inches(margin + (row * (cell_height + v_spacing)) + (cell_height - height.inches) / 2)
            
            img_stream = io.BytesIO(img_bytes)
            slide.shapes.add_picture(img_stream, left, top, width, height)
    
    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()

def create_zip_with_ppts(all_images: List[Tuple[str, bytes]], domain: str, images_per_slide: int, status_container, progress_bar) -> bytes:
    """Create a ZIP file containing multiple PPT files with progress updates"""
    from pptx.dml.color import RGBColor
    
    zip_buffer = io.BytesIO()
    num_batches = (len(all_images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
    
    status_container.write(f"🔄 Starting batch generation for {num_batches} PPT files...")
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for batch_idx in range(num_batches):
            # Calculate batch range
            start_idx = batch_idx * IMAGES_PER_PPT
            end_idx = min(start_idx + IMAGES_PER_PPT, len(all_images))
            batch_images = all_images[start_idx:end_idx]
            
            # Real-time status update BEFORE generation
            status_container.write(f"📊 Generating PPT {batch_idx + 1}/{num_batches} (images {start_idx + 1}-{end_idx})...")
            
            try:
                # Generate PPT for this batch
                ppt_bytes = generate_single_ppt(batch_images, domain, batch_idx + 1, images_per_slide)
                
                # Create unique filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{domain.replace('.', '_')}_batch_{batch_idx + 1}_of_{num_batches}_{timestamp}.pptx"
                
                # Write to ZIP
                zip_file.writestr(filename, ppt_bytes)
                
                # Update progress bar: 0.80 to 1.0
                batch_progress = 0.80 + (0.20 * (batch_idx + 1) / num_batches)
                progress_bar.progress(min(batch_progress, 1.0))
                
                # Success message AFTER generation
                num_slides = (len(batch_images) + images_per_slide - 1) // images_per_slide
                status_container.write(f"✅ Completed PPT {batch_idx + 1}/{num_batches} ({len(batch_images)} images, {num_slides} slides)")
                
            except Exception as e:
                status_container.write(f"⚠️ Error generating PPT {batch_idx + 1}: {str(e)[:100]}")
                raise
    
    status_container.write(f"🎉 All {num_batches} PPT files generated successfully!")
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    st.title("🛋️ E-Commerce Product Image to PowerPoint")
    st.markdown("**Multi-PPT Batch Generator** • Automatically creates multiple 50-image PPTs")
    
    with st.expander("ℹ️ How Batch Generation Works", expanded=False):
        st.info(f"""
        **Smart Batch Processing:**
        - Scrapes up to {MAX_TOTAL_IMAGES} product images
        - Automatically splits into batches of {IMAGES_PER_PPT} images
        - Each batch becomes a separate PPT file
        - All PPTs packaged in a single ZIP download
        - {4} images per slide (2×2 grid layout)
        
        **Example:** 150 images → 3 PPT files (50 images each) in one ZIP
        
        **Benefits:**
        - Faster processing (no waiting for huge files)
        - Easier to manage and share
        - More reliable on free tier
        """)
    
    url = st.text_input("🌐 Enter E-Commerce Website URL:", placeholder="https://example-furniture-store.com")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        generate_btn = st.button("🚀 Generate PPTs", type="primary", use_container_width=True)
    with col2:
        images_per_slide = st.selectbox("Images/Slide", [2, 3, 4], index=2)
    
    if generate_btn:
        if not url:
            st.error("Please enter a valid URL")
            return
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        progress_bar = st.progress(0)
        status_container = st.status("Starting batch scraping process...", expanded=True)
        
        try:
            scraper = ImageScraper(url)
            
            # Step 1: Analyze site
            progress_bar.progress(0.05)
            method = scraper.analyze_site(status_container)
            
            # Step 2: Crawl pages
            progress_bar.progress(0.10)
            pages = scraper.crawl_pages(status_container)
            
            # Step 3: Extract and filter images
            progress_bar.progress(0.15)
            all_candidates = []
            
            for i, page in enumerate(pages):
                status_container.write(f"📄 Scraping page {i+1}/{len(pages)}")
                candidates = scraper.extract_candidate_images(page)
                all_candidates.extend(candidates)
                progress_bar.progress(0.15 + (0.10 * (i+1) / len(pages)))
            
            status_container.write(f"🔍 Found {len(all_candidates)} candidate images")
            
            # Step 4: Filter product images
            progress_bar.progress(0.25)
            filtered_urls = scraper.filter_product_images(all_candidates)
            status_container.write(f"✅ Filtered to {len(filtered_urls)} product images")
            
            if not filtered_urls:
                status_container.update(label="❌ No product images found", state="error")
                st.error("No product images found. Try a different URL with visible product images.")
                return
            
            # Step 5: Download and validate (up to MAX_TOTAL_IMAGES)
            progress_bar.progress(0.30)
            status_container.write(f"⬇️ Downloading up to {MAX_TOTAL_IMAGES} images...")
            
            valid_images = scraper.download_and_validate_images(
                filtered_urls,
                progress_bar,
                status_container,
                max_images=MAX_TOTAL_IMAGES
            )
            
            if not valid_images:
                status_container.update(label="❌ No valid images downloaded", state="error")
                st.error("All images failed validation. The site may have anti-scraping measures.")
                return
            
            status_container.write(f"✅ Successfully downloaded {len(valid_images)} unique images")
            
            # Step 6: Generate multiple PPTs in batches
            progress_bar.progress(0.80)
            num_ppts = (len(valid_images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
            
            status_container.write(f"📊 Total images: {len(valid_images)}, Will create: {num_ppts} PPT file(s)")
            
            if num_ppts == 1:
                # Single PPT - direct download
                status_container.write(f"📊 Generating 1 PowerPoint file...")
                ppt_bytes = generate_single_ppt(valid_images, scraper.domain, 1, images_per_slide)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{scraper.domain.replace('.', '_')}_products_{timestamp}.pptx"
                
                progress_bar.progress(1.0)
                status_container.update(label="✅ Complete!", state="complete")
                
                num_slides = (len(valid_images) + images_per_slide - 1) // images_per_slide
                st.success(f"🎉 Created 1 PowerPoint with {len(valid_images)} images across {num_slides} slides!")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Images", len(valid_images))
                with col2:
                    st.metric("PPT Files", 1)
                with col3:
                    st.metric("File Size", f"{len(ppt_bytes) / 1024 / 1024:.1f} MB")
                
                st.download_button(
                    label="📥 Download PowerPoint",
                    data=ppt_bytes,
                    file_name=filename,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    type="primary"
                )
            else:
                # Multiple PPTs - create ZIP with live updates
                status_container.write(f"📊 Generating {num_ppts} PowerPoint files ({IMAGES_PER_PPT} images each)...")
                status_container.write(f"📦 This will create {num_ppts} separate PPT files automatically...")
                
                zip_bytes = create_zip_with_ppts(valid_images, scraper.domain, images_per_slide, status_container, progress_bar)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                zip_filename = f"{scraper.domain.replace('.', '_')}_products_{num_ppts}_files_{timestamp}.zip"
                
                status_container.update(label="✅ Complete!", state="complete")
                
                total_slides = (len(valid_images) + images_per_slide - 1) // images_per_slide
                st.success(f"🎉 Created {num_ppts} PowerPoint files with {len(valid_images)} total images!")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Images", len(valid_images))
                with col2:
                    st.metric("PPT Files", num_ppts)
                with col3:
                    st.metric("Images/PPT", IMAGES_PER_PPT)
                with col4:
                    st.metric("ZIP Size", f"{len(zip_bytes) / 1024 / 1024:.1f} MB")
                
                st.download_button(
                    label=f"📥 Download ZIP ({num_ppts} PPT files)",
                    data=zip_bytes,
                    file_name=zip_filename,
                    mime="application/zip",
                    type="primary"
                )
                
                with st.expander("📋 What's in the ZIP?"):
                    st.write(f"**{num_ppts} PowerPoint files:**")
                    for i in range(num_ppts):
                        start = i * IMAGES_PER_PPT
                        end = min(start + IMAGES_PER_PPT, len(valid_images))
                        slides = (end - start + images_per_slide - 1) // images_per_slide
                        st.write(f"- `batch_{i+1}.pptx` - Images {start+1}-{end} ({slides} slides)")
            
            # Preview
            st.subheader("🖼️ Preview (First 6 Images)")
            cols = st.columns(6)
            for i, (url, img_bytes) in enumerate(valid_images[:6]):
                with cols[i]:
                    st.image(img_bytes, use_container_width=True, caption=f"#{i+1}")
        
        except Exception as e:
            status_container.update(label=f"❌ Error: {str(e)}", state="error")
            st.error(f"Fatal error: {str(e)}")
            st.exception(e)

if __name__ == "__main__":
    main()
