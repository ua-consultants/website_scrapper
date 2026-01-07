import streamlit as st
import requests
from urllib.parse import urlparse
from PIL import Image
import io
import hashlib
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches
from pptx.dml.color import RGBColor
import time
import re
from typing import List, Tuple
import concurrent.futures
import zipfile

st.set_page_config(page_title="Shopify Image Scraper", page_icon="🛍️", layout="wide")

IMAGES_PER_PPT = 200
MAX_WORKERS = 5
TIMEOUT = 10
MAX_FILE_SIZE = 5 * 1024 * 1024
IMAGES_PER_SLIDE = 4

class ShopifyImageScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.domain = urlparse(base_url).netloc
        self.seen_hashes = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json,text/html,*/*',
        })
    
    def get_all_products(self, status_container):
        all_products = []
        page = 1
        
        status_container.write("🔍 Accessing Shopify product API...")
        
        while True:
            try:
                url = f"{self.base_url}/products.json?page={page}&limit=250"
                status_container.write(f"   📄 Fetching products page {page}...")
                
                response = self.session.get(url, timeout=TIMEOUT)
                
                if response.status_code in [503, 403, 404]:
                    status_container.write(f"   ⚠️ API error ({response.status_code}). Will try alternative method...")
                    return None
                
                response.raise_for_status()
                data = response.json()
                products = data.get('products', [])
                
                if not products:
                    break
                
                all_products.extend(products)
                status_container.write(f"   ✅ Found {len(products)} products (Total: {len(all_products)})")
                
                page += 1
                time.sleep(0.5)
                
                if page > 100:
                    break
                    
            except Exception as e:
                status_container.write(f"   ⚠️ Error: {str(e)[:60]}")
                return None
        
        if all_products:
            status_container.write(f"✅ Retrieved {len(all_products)} total products from API")
        return all_products if all_products else None
    
    def get_images_from_collections(self, status_container):
        all_image_urls = []
        
        status_container.write("🔄 Trying alternative method: Scraping collections...")
        
        collection_urls = [
            f"{self.base_url}/collections/all",
            f"{self.base_url}/collections/all-products",
            f"{self.base_url}/products",
        ]
        
        for coll_url in collection_urls:
            try:
                status_container.write(f"   📄 Trying {coll_url}...")
                response = self.session.get(coll_url, timeout=TIMEOUT)
                
                if response.status_code == 200:
                    status_container.write(f"   ✅ Accessible! Extracting images...")
                    html = response.text
                    
                    cdn_images = re.findall(r'https://cdn\.shopify\.com/s/files/[^"\s<>\']+', html, re.IGNORECASE)
                    all_image_urls.extend(cdn_images)
                    
                    status_container.write(f"   ✅ Found {len(cdn_images)} images")
                    
                    if len(all_image_urls) > 10:
                        break
                        
            except Exception as e:
                status_container.write(f"   ⚠️ Failed: {str(e)[:60]}")
                continue
        
        unique_urls = list(set(all_image_urls))
        
        if unique_urls:
            status_container.write(f"✅ Extracted {len(unique_urls)} image URLs via fallback")
        
        return unique_urls
    
    def extract_image_urls_from_products(self, products, status_container):
        image_urls = []
        
        status_container.write(f"🔍 Extracting images from {len(products)} products...")
        
        for product in products:
            try:
                if 'images' in product:
                    for img in product['images']:
                        if 'src' in img:
                            image_urls.append(img['src'])
                
                if 'variants' in product:
                    for variant in product['variants']:
                        if 'image_id' in variant and variant.get('image_id'):
                            for img in product.get('images', []):
                                if img.get('id') == variant['image_id'] and 'src' in img:
                                    image_urls.append(img['src'])
                
                if 'image' in product and product['image']:
                    if 'src' in product['image']:
                        image_urls.append(product['image']['src'])
                        
            except Exception:
                pass
        
        unique_urls = list(set(image_urls))
        status_container.write(f"✅ Extracted {len(unique_urls)} unique image URLs")
        
        return unique_urls
    
    def download_and_validate_image(self, url: str) -> Tuple[str, bytes]:
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
            
            img = Image.open(io.BytesIO(img_bytes))
            
            if img.width < 100 or img.height < 100:
                return None
            
            max_dimension = 1920
            if img.width > max_dimension or img.height > max_dimension:
                ratio = min(max_dimension/img.width, max_dimension/img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                if img.mode in ('RGBA', 'LA'):
                    background.paste(img, mask=img.split()[-1])
                else:
                    background.paste(img)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85, optimize=True)
            final_bytes = output.getvalue()
            
            self.seen_hashes.add(img_hash)
            return (url, final_bytes)
                
        except:
            return None
    
    def download_all_images(self, image_urls: List[str], status_container):
        valid_images = []
        total = len(image_urls)
        
        status_container.write(f"⬇️ Downloading {total} images...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self.download_and_validate_image, url): url for url in image_urls}
            
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                result = future.result()
                
                if result:
                    valid_images.append(result)
                    st.session_state.downloaded_images = valid_images.copy()
                    
                    if len(valid_images) % 20 == 0:
                        status_container.write(f"   ✅ Downloaded {len(valid_images)} images ({completed}/{total} processed)")
        
        status_container.write(f"✅ Downloaded {len(valid_images)} valid images!")
        return valid_images

def generate_ppt(images: List[Tuple[str, bytes]], batch_num: int, total_batches: int, domain: str) -> bytes:
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625)
    blank_layout = prs.slide_layouts[6]
    
    for i in range(0, len(images), IMAGES_PER_SLIDE):
        batch = images[i:i + IMAGES_PER_SLIDE]
        slide = prs.slides.add_slide(blank_layout)
        
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

def create_all_ppts(images: List[Tuple[str, bytes]], domain: str, status_container):
    num_batches = (len(images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
    
    if num_batches == 1:
        status_container.write(f"📊 Generating PowerPoint with {len(images)} images...")
        ppt_bytes = generate_ppt(images, 1, 1, domain)
        status_container.write(f"✅ PowerPoint generated!")
        return ppt_bytes
    
    status_container.write(f"📦 Creating {num_batches} PowerPoint files...")
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for batch_idx in range(num_batches):
            start_idx = batch_idx * IMAGES_PER_PPT
            end_idx = min(start_idx + IMAGES_PER_PPT, len(images))
            batch_images = images[start_idx:end_idx]
            
            status_container.write(f"📊 Generating PPT {batch_idx + 1}/{num_batches}...")
            
            ppt_bytes = generate_ppt(batch_images, batch_idx + 1, num_batches, domain)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{domain.replace('.', '_')}_batch_{batch_idx + 1}_{timestamp}.pptx"
            
            zip_file.writestr(filename, ppt_bytes)
            
            num_slides = (len(batch_images) + IMAGES_PER_SLIDE - 1) // IMAGES_PER_SLIDE
            status_container.write(f"✅ Completed PPT {batch_idx + 1}/{num_batches} ({len(batch_images)} imgs, {num_slides} slides)")
    
    status_container.write(f"🎉 All {num_batches} PowerPoint files created!")
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    st.title("🛍️ Shopify Store Image Scraper")
    st.markdown("**Dual-Method System** • API + Fallback Scraping • Works with restricted stores")
    
    if 'downloaded_images' not in st.session_state:
        st.session_state.downloaded_images = []
    if 'scraper_domain' not in st.session_state:
        st.session_state.scraper_domain = None
    if 'is_scraping' not in st.session_state:
        st.session_state.is_scraping = False
    
    url = st.text_input("🌐 Enter Shopify Store URL:", placeholder="https://thepurplepony.com")
    
    num_cached = len(st.session_state.downloaded_images)
    
    if st.session_state.is_scraping:
        st.warning(f"⚡ **Scraping... {num_cached} images downloaded!** You can download PPT anytime!")
    elif not st.session_state.downloaded_images:
        st.info("ℹ️ **Button activates as images download**")
    else:
        st.success(f"✅ **{num_cached} images ready!**")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        scrape_button = st.button("🚀 Scrape Images", type="primary", use_container_width=True)
    
    with col2:
        if st.session_state.downloaded_images:
            num_images = len(st.session_state.downloaded_images)
            icon = "⚡" if st.session_state.is_scraping else "📥"
            download_ppt_button = st.button(f"{icon} Download PPT ({num_images})", use_container_width=True)
        else:
            download_ppt_button = st.button("📥 Download PPT (0)", disabled=True, use_container_width=True)
    
    with col3:
        if st.session_state.is_scraping or st.session_state.downloaded_images:
            if st.button("🔄 Refresh", use_container_width=True):
                st.rerun()
    
    if download_ppt_button and st.session_state.downloaded_images:
        with st.spinner("Generating PowerPoint..."):
            try:
                ppt_status = st.status("Generating...", expanded=True)
                result_bytes = create_all_ppts(st.session_state.downloaded_images, 
                                              st.session_state.scraper_domain or "shopify", 
                                              ppt_status)
                ppt_status.update(label="✅ Ready!", state="complete")
                
                num_ppts = (len(st.session_state.downloaded_images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                if num_ppts == 1:
                    filename = f"shopify_{timestamp}.pptx"
                    st.download_button("📥 Click to Download", result_bytes, filename, 
                                     "application/vnd.openxmlformats-officedocument.presentationml.presentation", 
                                     key="dl1")
                else:
                    filename = f"shopify_{num_ppts}files_{timestamp}.zip"
                    st.download_button(f"📥 Download ZIP ({num_ppts} files)", result_bytes, filename, 
                                     "application/zip", key="dl2")
            except Exception as e:
                st.error(f"Error: {e}")
    
    if scrape_button:
        if not url:
            st.error("Enter a URL")
            return
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        st.session_state.is_scraping = True
        
        progress_bar = st.progress(0)
        status_container = st.status("Starting...", expanded=True)
        
        try:
            scraper = ShopifyImageScraper(url)
            st.session_state.scraper_domain = scraper.domain
            
            status_container.write("🔍 Phase 1: Fetching products...")
            progress_bar.progress(0.1)
            
            products = scraper.get_all_products(status_container)
            image_urls = []
            
            if not products:
                status_container.write("🔄 Trying fallback...")
                progress_bar.progress(0.2)
                image_urls = scraper.get_images_from_collections(status_container)
                
                if not image_urls:
                    st.session_state.is_scraping = False
                    status_container.update(label="❌ No images", state="error")
                    st.error("Unable to extract images. Try a different store.")
                    return
            else:
                progress_bar.progress(0.3)
                image_urls = scraper.extract_image_urls_from_products(products, status_container)
            
            if not image_urls:
                st.session_state.is_scraping = False
                status_container.update(label="❌ No images", state="error")
                st.error("No images found")
                return
            
            progress_bar.progress(0.5)
            status_container.write(f"⬇️ Phase 3: Downloading {len(image_urls)} images...")
            
            valid_images = scraper.download_all_images(image_urls, status_container)
            
            if not valid_images:
                st.session_state.is_scraping = False
                status_container.update(label="❌ No valid images", state="error")
                st.error("Download failed")
                return
            
            progress_bar.progress(0.8)
            
            st.session_state.downloaded_images = valid_images
            st.session_state.scraper_domain = scraper.domain
            
            result_bytes = create_all_ppts(valid_images, scraper.domain, status_container)
            
            progress_bar.progress(1.0)
            st.session_state.is_scraping = False
            status_container.update(label="✅ Complete!", state="complete")
            
            num_ppts = (len(valid_images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
            
            st.balloons()
            
            if num_ppts == 1:
                st.success(f"🎉 {len(valid_images)} images!")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{scraper.domain.replace('.', '_')}_{timestamp}.pptx"
                st.download_button("📥 Download", result_bytes, filename,
                                 "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                 type="primary", key="main1")
            else:
                st.success(f"🎉 {len(valid_images)} images in {num_ppts} PPTs!")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{scraper.domain.replace('.', '_')}_{num_ppts}files_{timestamp}.zip"
                st.download_button(f"📥 Download ZIP ({num_ppts} files)", result_bytes, filename,
                                 "application/zip", type="primary", key="main2")
            
            st.subheader("Preview")
            cols = st.columns(8)
            for i, (url, img_bytes) in enumerate(valid_images[:8]):
                with cols[i]:
                    st.image(img_bytes, use_container_width=True)
        
        except Exception as e:
            st.session_state.is_scraping = False
            status_container.update(label=f"❌ Error", state="error")
            st.error(f"Error: {e}")
            st.exception(e)

if __name__ == "__main__":
    main()
