import urllib.request
import json
import re
import xml.etree.ElementTree as ET
import sys
import gzip
import io
from difflib import get_close_matches

# === CONFIGURATION ===
M3U_URL = "https://gist.githubusercontent.com/CiroHoodLove/ba36db853c30c47a3480020e87a352e6/raw/5e6e350293ec1c9ac7fa25fe1c0c6e5b3c3fabab/playlist.m3u"
EPG_SOURCES = [
    "https://iptv-org.github.io/epg/xml/fr.xml", # France
    "https://iptv-org.github.io/epg/xml/uk.xml", # UK
    "https://iptv-org.github.io/epg/xml/es.xml", # Spain
    "https://iptv-org.github.io/epg/xml/de.xml"  # Germany
]
OUTPUT_FILENAME = "custom_epg.xml"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

def fetch_url(url, description):
    print(f"Downloading {description}...")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req) as response:
            content = response.read()
            if url.endswith('.gz'):
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
                    return f.read()
            return content
    except Exception as e:
        print(f"!!! Error downloading {description}: {e}")
        return None

def aggressive_clean(text):
    if not text: return ""
    text = text.lower()
    
    # 1. Remove specific junk words
    junk = ['fhd', 'hd', 'hevc', 'h265', 'vip', '4k', 'backup', 'low', 'mobile', 'sd', 'vod', 'series']
    for j in junk:
        text = text.replace(j, '')
        
    # 2. Remove any 2-3 letter country prefix like "FR:", "UK -", "DE |"
    # This regex looks for 2-3 letters at start followed by non-letters
    text = re.sub(r'^[a-z]{2,3}[^a-z0-9]+', '', text)
    
    # 3. Keep only letters and numbers
    return re.sub(r'[^a-z0-9]', '', text)

def main():
    # 1. Fetch M3U
    m3u_bytes = fetch_url(M3U_URL, "Your Playlist")
    if not m3u_bytes: sys.exit(1)

    my_channels = {} # { CleanName : TVG-ID }
    
    lines = m3u_bytes.decode('utf-8', errors='ignore').splitlines()
    for line in lines:
        if line.startswith('#EXTINF'):
            # Extract TVG-ID
            id_match = re.search(r'tvg-id="([^"]+)"', line)
            if not id_match: continue
            tvg_id = id_match.group(1)
            
            # TRY 1: Check for tvg-name="..." (Usually cleaner)
            name_match = re.search(r'tvg-name="([^"]+)"', line)
            if name_match:
                clean = aggressive_clean(name_match.group(1))
                my_channels[clean] = tvg_id
                
            # TRY 2: Use the Display Name (After comma)
            display_part = line.strip().split(',')[-1]
            clean_display = aggressive_clean(display_part)
            my_channels[clean_display] = tvg_id

    print(f"✅ Loaded {len(my_channels)} channels from M3U.")
    print(f"   Sample Clean Names: {list(my_channels.keys())[:5]}")

    # 2. Process EPGs
    master_root = ET.Element("tv")
    master_root.set("generator-info-name", "Custom-Automator")
    
    total_matches = 0
    seen_ids = set()

    for url in EPG_SOURCES:
        xml_bytes = fetch_url(url, "EPG Source")
        if not xml_bytes: continue
        
        try:
            root = ET.fromstring(xml_bytes)
            file_matches = 0
            
            # Map XML_ID -> Your_ID
            id_map = {} 
            
            for channel in root.findall('channel'):
                original_xml_id = channel.get('id')
                display_name = channel.find('display-name').text
                
                clean_xml = aggressive_clean(display_name)
                
                # Try Exact Match
                match_id = my_channels.get(clean_xml)
                
                # Try Fuzzy Match
                if not match_id:
                    matches = get_close_matches(clean_xml, my_channels.keys(), n=1, cutoff=0.8)
                    if matches:
                        match_id = my_channels[matches[0]]

                if match_id:
                    # We found a match!
                    if match_id not in seen_ids:
                        channel.set('id', match_id)
                        master_root.append(channel)
                        
                        id_map[original_xml_id] = match_id
                        seen_ids.add(match_id)
                        file_matches += 1
                        total_matches += 1

            # Copy Programmes for matched channels
            if file_matches > 0:
                print(f"   -> Matched {file_matches} channels in this file.")
                for prog in root.findall('programme'):
                    if prog.get('channel') in id_map:
                        prog.set('channel', id_map[prog.get('channel')])
                        master_root.append(prog)
            else:
                print(f"   ⚠️ No matches found in {url}. Check naming?")
                print(f"      Sample XML Names: {[aggressive_clean(c.find('display-name').text) for c in root.findall('channel')[:5]]}")

        except Exception as e:
            print(f"XML Error: {e}")

    # 3. Save
    if total_matches == 0:
        print("\n❌ CRITICAL: 0 Matches found. The file will be empty.")
        print("This means the cleaner logic didn't link 'FR TF1' to 'TF1'.")
    else:
        tree = ET.ElementTree(master_root)
        tree.write(OUTPUT_FILENAME, encoding='UTF-8', xml_declaration=True)
        print(f"\n🚀 SUCCESS! Saved {total_matches} channels to {OUTPUT_FILENAME}")

if __name__ == "__main__":
    main()
