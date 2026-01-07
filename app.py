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
from typing import List, Tuple
import concurrent.futures
import zipfile
import json

# Page config
st.set_page_config(page_title="Shopify Image Scraper", page_icon="🖼️", layout="wide")

# Constants
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
        """Get all products using Shopify's public API"""
        all_products = []
        page = 1
        
        status_container.write("🔍 Accessing Shopify product API...")
        
        while True:
            try:
                # Shopify's public JSON endpoint
                url = f"{self.base_url}/products.json?page={page}&limit=250"
                status_container.write(f"   📄 Fetching products page {page}...")
                
                response = self.session.get(url, timeout=TIMEOUT)
                
                if response.status_code == 404:
                    status_container.write("   ⚠️ Not a Shopify store or API not accessible")
                    break
                
                response.raise_for_status()
                data = response.json()
                
                products = data.get('products', [])
                
                if not products:
                    break
                
                all_products.extend(products)
                status_container.write(f"   ✅ Found {len(products)} products on page {page} (Total: {len(all_products)})")
                
                page += 1
                time.sleep(0.5)  # Be polite
                
                # Safety limit
                if page > 100:
                    break
                    
            except Exception as e:
                status_container.write(f"   ⚠️ Error on page {page}: {str(e)[:80]}")
                break
        
        status_container.write(f"✅ Retrieved {len(all_products)} total products from API")
        return all_products
    
    def extract_all_image_urls(self, products, status_container):
        """Extract all image URLs from product data"""
        image_urls = []
        
        status_container.write(f"🔍 Extracting images from {len(products)} products...")
        
        for product in products:
            try:
                # Main product images
                if 'images' in product:
                    for img in product['images']:
                        if 'src' in img:
                            image_urls.append(img['src'])
                
                # Variant images
                if 'variants' in product:
                    for variant in product['variants']:
                        if 'image_id' in variant and variant.get('image_id'):
                            # Find the image by ID
                            for img in product.get('images', []):
                                if img.get('id') == variant['image_id'] and 'src' in img:
                                    image_urls.append(img['src'])
                
                # Product image (single)
                if 'image' in product and product['image']:
                    if 'src' in product['image']:
                        image_urls.append(product['image']['src'])
                        
            except Exception:
                pass
        
        # Remove duplicates
        unique_urls = list(set(image_urls))
        status_container.write(f"✅ Extracted {len(unique_urls)} unique image URLs")
        
        return unique_urls
    
    def download_and_validate_image(self, url: str) -> Tuple[str, bytes] or None:
        """Download and validate a single image"""
        try:
            response = self.session.get(url, timeout=TIMEOUT, stream=True)
            response.raise_for_status()
            
            # Check size
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > MAX_FILE_SIZE:
                return None
            
            # Download
            img_bytes = b''
            for chunk in response.iter_content(chunk_size=8192):
                img_bytes += chunk
                if len(img_bytes) > MAX_FILE_SIZE:
                    return None
            
            # Check hash
            img_hash = hashlib.sha256(img_bytes).hexdigest()
            if img_hash in self.seen_hashes:
                return None
            
            # Validate as image
            img = Image.open(io.BytesIO(img_bytes))
            
            # Skip tiny images
            if img.width < 100 or img.height < 100:
                return None
            
            # Resize if too large
            max_dimension = 1920
            if img.width > max_dimension or img.height > max_dimension:
                ratio = min(max_dimension/img.width, max_dimension/img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Convert to RGB
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
            
            # Save to bytes
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85, optimize=True)
            final_bytes = output.getvalue()
            
            self.seen_hashes.add(img_hash)
            return (url, final_bytes)
                
        except:
            return None
    
    def download_all_images(self, image_urls: List[str], status_container):
        """Download all images with progress tracking"""
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
                    if len(valid_images) % 20 == 0:
                        status_container.write(f"   ✅ Downloaded {len(valid_images)} valid images ({completed}/{total} processed)")
        
        status_container.write(f"✅ Successfully downloaded {len(valid_images)} valid images!")
        return valid_images

def generate_ppt(images: List[Tuple[str, bytes]], batch_num: int, total_batches: int, domain: str) -> bytes:
    """Generate a single PowerPoint file"""
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
    """Create multiple PPT files and package in ZIP"""
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
            
            status_container.write(f"📊 Generating PPT {batch_idx + 1}/{num_batches} (images {start_idx + 1}-{end_idx})...")
            
            ppt_bytes = generate_ppt(batch_images, batch_idx + 1, num_batches, domain)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{domain.replace('.', '_')}_batch_{batch_idx + 1}_of_{num_batches}_{timestamp}.pptx"
            
            zip_file.writestr(filename, ppt_bytes)
            
            num_slides = (len(batch_images) + IMAGES_PER_SLIDE - 1) // IMAGES_PER_SLIDE
            status_container.write(f"✅ Completed PPT {batch_idx + 1}/{num_batches} ({len(batch_images)} images, {num_slides} slides)")
    
    status_container.write(f"🎉 All {num_batches} PowerPoint files created!")
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    st.title("🛍️ Shopify Store Image Scraper")
    st.markdown("**Uses Shopify's Public API** • Works with ANY Shopify store • No bot detection issues")
    
    with st.expander("ℹ️ How It Works", expanded=False):
        st.info("""
        **Shopify API Scraping:**
        - Uses Shopify's official public `/products.json` API
        - Works with ANY Shopify store (no bot detection)
        - Extracts ALL product images automatically
        - Downloads and validates all images
        - Creates PPT files with 200 images each (4 per slide)
        - Automatically packages multiple PPTs in ZIP
        
        **Perfect for:**
        - Shopify stores (thepurplepony.com ✅)
        - Product catalogs
        - E-commerce sites on Shopify
        
        **Note:** Only works with Shopify-powered websites.
        """)
    
    url = st.text_input("🌐 Enter Shopify Store URL:", placeholder="https://thepurplepony.com")
    
    if st.button("🚀 Scrape Shopify Images", type="primary"):
        if not url:
            st.error("Please enter a valid URL")
            return
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        progress_bar = st.progress(0)
        status_container = st.status("Starting Shopify API scraping...", expanded=True)
        
        try:
            scraper = ShopifyImageScraper(url)
            
            # Step 1: Get products from API
            status_container.write("🔍 Phase 1: Fetching products from Shopify API...")
            progress_bar.progress(0.1)
            
            products = scraper.get_all_products(status_container)
            
            if not products:
                status_container.update(label="❌ No products found", state="error")
                st.error("""
                No products found. This could mean:
                - The site is not a Shopify store
                - The Shopify API is not publicly accessible
                - The URL is incorrect
                
                Try:
                - Verifying the URL is correct
                - Checking if it's a Shopify store (look for "myshopify.com" in page source)
                - Trying the homepage URL
                """)
                return
            
            progress_bar.progress(0.3)
            
            # Step 2: Extract image URLs
            status_container.write(f"🔍 Phase 2: Extracting images from products...")
            image_urls = scraper.extract_all_image_urls(products, status_container)
            
            if not image_urls:
                status_container.update(label="❌ No images found", state="error")
                st.error("Products found but no images extracted.")
                return
            
            progress_bar.progress(0.5)
            
            # Step 3: Download images
            status_container.write(f"⬇️ Phase 3: Downloading {len(image_urls)} images...")
            valid_images = scraper.download_all_images(image_urls, status_container)
            
            if not valid_images:
                status_container.update(label="❌ No valid images", state="error")
                st.error("Images found but all failed download/validation.")
                return
            
            progress_bar.progress(0.8)
            
            # Step 4: Generate PPTs
            status_container.write("📊 Phase 4: Generating PowerPoint presentations...")
            result_bytes = create_all_ppts(valid_images, scraper.domain, status_container)
            
            progress_bar.progress(1.0)
            status_container.update(label="✅ Complete!", state="complete")
            
            # Display results
            num_ppts = (len(valid_images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
            
            if num_ppts == 1:
                st.success(f"🎉 Created PowerPoint with {len(valid_images)} images!")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Total Images", len(valid_images))
                with col2:
                    st.metric("Total Slides", (len(valid_images) + IMAGES_PER_SLIDE - 1) // IMAGES_PER_SLIDE)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{scraper.domain.replace('.', '_')}_shopify_images_{timestamp}.pptx"
                
                st.download_button(
                    label="📥 Download PowerPoint",
                    data=result_bytes,
                    file_name=filename,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    type="primary"
                )
            else:
                st.success(f"🎉 Created {num_ppts} PowerPoint files with {len(valid_images)} total images!")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Images", len(valid_images))
                with col2:
                    st.metric("PPT Files", num_ppts)
                with col3:
                    st.metric("ZIP Size", f"{len(result_bytes) / 1024 / 1024:.1f} MB")
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{scraper.domain.replace('.', '_')}_shopify_{num_ppts}_files_{timestamp}.zip"
                
                st.download_button(
                    label=f"📥 Download ZIP ({num_ppts} PPT files)",
                    data=result_bytes,
                    file_name=filename,
                    mime="application/zip",
                    type="primary"
                )
                
                with st.expander("📋 What's in the ZIP?"):
                    for i in range(num_ppts):
                        start = i * IMAGES_PER_PPT + 1
                        end = min((i + 1) * IMAGES_PER_PPT, len(valid_images))
                        slides = (end - start + 1 + IMAGES_PER_SLIDE - 1) // IMAGES_PER_SLIDE
                        st.write(f"- `batch_{i+1}.pptx` - Images {start}-{end} ({slides} slides)")
            
            # Preview
            st.subheader("🖼️ Preview (First 8 Images)")
            cols = st.columns(8)
            for i, (url, img_bytes) in enumerate(valid_images[:8]):
                with cols[i]:
                    st.image(img_bytes, use_container_width=True)
        
        except Exception as e:
            status_container.update(label=f"❌ Error: {str(e)}", state="error")
            st.error(f"Fatal error: {str(e)}")
            st.exception(e)

if __name__ == "__main__":
    main()
