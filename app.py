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

st.set_page_config(page_title="Shopify Scraper", page_icon="🛍️", layout="wide")

IMAGES_PER_PPT = 200
MAX_WORKERS = 5
TIMEOUT = 10
MAX_FILE_SIZE = 5 * 1024 * 1024
IMAGES_PER_SLIDE = 4

class ShopifyImageScraper:
    def __init__(self, base_url):
        self.base_url = base_url
        self.domain = urlparse(base_url).netloc
        self.seen_hashes = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json,text/html,*/*',
        })
    
    def get_products(self, status_container):
        all_products = []
        page = 1
        
        status_container.write("🔍 Trying Shopify API...")
        
        while page <= 5:
            try:
                url = f"{self.base_url}/products.json?page={page}&limit=250"
                status_container.write(f"   Page {page}...")
                
                response = self.session.get(url, timeout=TIMEOUT)
                
                if response.status_code in [503, 403, 404]:
                    status_container.write(f"   API blocked ({response.status_code})")
                    return None
                
                response.raise_for_status()
                data = response.json()
                products = data.get('products', [])
                
                if not products:
                    break
                
                all_products.extend(products)
                status_container.write(f"   Found {len(products)} products")
                page += 1
                time.sleep(0.5)
                    
            except Exception as e:
                status_container.write(f"   Error: {str(e)[:50]}")
                return None
        
        if all_products:
            status_container.write(f"✅ Got {len(all_products)} products from API")
        return all_products if all_products else None
    
    def scrape_collections(self, status_container):
        image_urls = []
        
        status_container.write("🔄 Trying collection pages...")
        
        urls = [
            f"{self.base_url}/collections/all",
            f"{self.base_url}/products",
        ]
        
        for url in urls:
            try:
                status_container.write(f"   Trying {url}...")
                response = self.session.get(url, timeout=TIMEOUT)
                
                if response.status_code == 200:
                    html = response.text
                    cdn_urls = re.findall(r'https://cdn\.shopify\.com/s/files/[^"\s<>\']+', html)
                    image_urls.extend(cdn_urls)
                    status_container.write(f"   Found {len(cdn_urls)} images")
                    
                    if len(image_urls) > 10:
                        break
                        
            except:
                continue
        
        unique = list(set(image_urls))
        if unique:
            status_container.write(f"✅ Got {len(unique)} images from scraping")
        return unique
    
    def extract_images_from_products(self, products):
        urls = []
        for product in products:
            try:
                if 'images' in product:
                    for img in product['images']:
                        if 'src' in img:
                            urls.append(img['src'])
            except:
                pass
        return list(set(urls))
    
    def download_image(self, url):
        try:
            response = self.session.get(url, timeout=TIMEOUT, stream=True)
            response.raise_for_status()
            
            img_bytes = b''
            for chunk in response.iter_content(8192):
                img_bytes += chunk
                if len(img_bytes) > MAX_FILE_SIZE:
                    return None
            
            img_hash = hashlib.sha256(img_bytes).hexdigest()
            if img_hash in self.seen_hashes:
                return None
            
            img = Image.open(io.BytesIO(img_bytes))
            
            if img.width < 100 or img.height < 100:
                return None
            
            if img.width > 1920 or img.height > 1920:
                ratio = min(1920/img.width, 1920/img.height)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
            
            if img.mode != 'RGB':
                if img.mode in ('RGBA', 'LA', 'P'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode in ('RGBA', 'LA'):
                        bg.paste(img, mask=img.split()[-1])
                    else:
                        bg.paste(img)
                    img = bg
                else:
                    img = img.convert('RGB')
            
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=85)
            self.seen_hashes.add(img_hash)
            return (url, out.getvalue())
        except:
            return None
    
    def download_all(self, urls, status_container):
        valid = []
        status_container.write(f"⬇️ Downloading {len(urls)} images...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self.download_image, url): url for url in urls}
            
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                result = future.result()
                if result:
                    valid.append(result)
                    st.session_state.downloaded_images = valid.copy()
                    if len(valid) % 20 == 0:
                        status_container.write(f"   ✅ {len(valid)} images")
        
        status_container.write(f"✅ Downloaded {len(valid)} images!")
        return valid

def make_ppt(images, domain):
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625)
    
    for i in range(0, len(images), 4):
        batch = images[i:i+4]
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        
        bg = slide.background
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(255, 255, 255)
        
        positions = [(0,0), (1,0), (0,1), (1,1)]
        
        for idx, (url, img_bytes) in enumerate(batch):
            if idx >= len(positions):
                break
            col, row = positions[idx]
            
            img = Image.open(io.BytesIO(img_bytes))
            w, h = img.size
            ratio = w / h
            
            cell_w, cell_h = 4.5, 2.3
            
            if ratio > cell_w/cell_h:
                width = Inches(cell_w)
                height = Inches(cell_w / ratio)
            else:
                height = Inches(cell_h)
                width = Inches(cell_h * ratio)
            
            left = Inches(0.3 + col * 4.8 + (cell_w - width.inches)/2)
            top = Inches(0.3 + row * 2.5 + (cell_h - height.inches)/2)
            
            slide.shapes.add_picture(io.BytesIO(img_bytes), left, top, width, height)
    
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()

def make_zip(images, domain, status_container):
    num = (len(images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
    
    if num == 1:
        return make_ppt(images, domain)
    
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w') as zf:
        for i in range(num):
            start = i * IMAGES_PER_PPT
            end = min(start + IMAGES_PER_PPT, len(images))
            batch = images[start:end]
            
            status_container.write(f"Making PPT {i+1}/{num}...")
            ppt = make_ppt(batch, domain)
            zf.writestr(f"batch_{i+1}.pptx", ppt)
    
    zip_buf.seek(0)
    return zip_buf.getvalue()

def main():
    st.title("🛍️ Shopify Store Scraper")
    
    if 'downloaded_images' not in st.session_state:
        st.session_state.downloaded_images = []
    if 'domain' not in st.session_state:
        st.session_state.domain = None
    if 'scraping' not in st.session_state:
        st.session_state.scraping = False
    
    url = st.text_input("Store URL:", "https://thepurplepony.com")
    
    num = len(st.session_state.downloaded_images)
    
    if st.session_state.scraping:
        st.warning(f"⚡ Scraping... {num} images so far!")
    elif num > 0:
        st.success(f"✅ {num} images ready!")
    else:
        st.info("Enter URL and click Scrape")
    
    col1, col2, col3 = st.columns([2,1,1])
    
    with col1:
        scrape = st.button("🚀 Scrape", type="primary", use_container_width=True)
    with col2:
        if num > 0:
            download = st.button(f"📥 PPT ({num})", use_container_width=True)
        else:
            download = st.button("📥 PPT (0)", disabled=True, use_container_width=True)
    with col3:
        if st.session_state.scraping or num > 0:
            if st.button("🔄", use_container_width=True):
                st.rerun()
    
    if download and num > 0:
        with st.spinner("Making PPT..."):
            status = st.status("Generating...")
            result = make_zip(st.session_state.downloaded_images, 
                            st.session_state.domain or "store", status)
            status.update(label="Done!", state="complete")
            
            n_ppts = (num + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
            if n_ppts == 1:
                st.download_button("Download", result, "images.pptx", key="d1")
            else:
                st.download_button(f"Download ZIP ({n_ppts} PPTs)", result, "images.zip", key="d2")
    
    if scrape:
        if not url:
            st.error("Enter URL")
            return
        
        if not url.startswith('http'):
            url = 'https://' + url
        
        st.session_state.scraping = True
        
        prog = st.progress(0)
        status = st.status("Starting...", expanded=True)
        
        try:
            scraper = ShopifyImageScraper(url)
            st.session_state.domain = scraper.domain
            
            prog.progress(0.1)
            products = scraper.get_products(status)
            
            urls = []
            if not products:
                prog.progress(0.2)
                urls = scraper.scrape_collections(status)
                if not urls:
                    st.session_state.scraping = False
                    status.update(label="❌ Failed", state="error")
                    st.error("No images found")
                    return
            else:
                prog.progress(0.3)
                urls = scraper.extract_images_from_products(products)
            
            if not urls:
                st.session_state.scraping = False
                status.update(label="❌ No images", state="error")
                return
            
            prog.progress(0.5)
            images = scraper.download_all(urls, status)
            
            if not images:
                st.session_state.scraping = False
                status.update(label="❌ Download failed", state="error")
                return
            
            prog.progress(0.8)
            st.session_state.downloaded_images = images
            
            result = make_zip(images, scraper.domain, status)
            
            prog.progress(1.0)
            st.session_state.scraping = False
            status.update(label="✅ Done!", state="complete")
            
            st.balloons()
            st.success(f"Got {len(images)} images!")
            
            n = (len(images) + IMAGES_PER_PPT - 1) // IMAGES_PER_PPT
            if n == 1:
                st.download_button("📥 Download PPT", result, "shopify.pptx", key="m1")
            else:
                st.download_button(f"📥 Download ZIP ({n} PPTs)", result, "shopify.zip", key="m2")
            
        except Exception as e:
            st.session_state.scraping = False
            st.error(f"Error: {e}")

if __name__ == "__main__":
    main()
