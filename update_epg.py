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
API_CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"

# STABLE EPG LINKS (Direct from iptv-org's new structure)
EPG_SOURCES = [
    "https://iptv-org.github.io/epg/xml/fr.xml", # France
    "https://iptv-org.github.io/epg/xml/uk.xml", # UK
    "https://iptv-org.github.io/epg/xml/es.xml", # Spain
    "https://iptv-org.github.io/epg/xml/de.xml"  # Germany
]

OUTPUT_FILENAME = "custom_epg.xml"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

def fetch_url(url, description):
    print(f"Downloading {description} from: {url}")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req) as response:
            content = response.read()
            # Auto-decompress if it's GZIP
            if url.endswith('.gz'):
                print("   -> Decompressing GZIP...")
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
                    return f.read()
            return content
    except Exception as e:
        print(f"!!! WARNING: Could not download {description}: {e}")
        return None # Return None instead of crashing, so other files can still load

def normalize(text):
    if not text: return ""
    text = text.lower()
    # Remove junk to help matching
    for word in ['fr', 'fr:', 'fr-', 'uk', 'es', 'de', 'fhd', 'hd', 'hevc', 'vip', '4k', 'backup', '|', '(', ')']:
        text = text.replace(word, '')
    return re.sub(r'[^a-z0-9]', '', text)

def main():
    # 1. Fetch API (The Dictionary)
    data_bytes = fetch_url(API_CHANNELS_URL, "Channel Database")
    if not data_bytes: sys.exit(1)
    
    data = json.loads(data_bytes.decode())
    api_lookup = {}
    
    # Filter for the countries you want (FR, UK, ES, DE)
    target_countries = ['FR', 'UK', 'ES', 'DE', 'GB'] 
    
    for item in data:
        if item.get('country') in target_countries:
            cid = item.get('id')
            api_lookup[normalize(item.get('name'))] = cid
            for alt in item.get('alt_names', []):
                api_lookup[normalize(alt)] = cid

    # 2. Fetch YOUR Playlist
    m3u_bytes = fetch_url(M3U_URL, "Your Playlist")
    if not m3u_bytes: sys.exit(1)

    my_channels = {}
    for line in m3u_bytes.decode('utf-8', errors='ignore').splitlines():
        if line.startswith('#EXTINF'):
            tvg_match = re.search(r'tvg-id="([^"]+)"', line)
            name_part = line.strip().split(',')[-1]
            if tvg_match and name_part:
                my_channels[normalize(name_part)] = tvg_match.group(1)
    
    print(f"Found {len(my_channels)} channels in your playlist.")

    # 3. Create Master XML Structure
    master_root = ET.Element("tv")
    master_root.set("generator-info-name", "Custom-Automator")
    
    seen_ids = set()
    total_matches = 0

    # 4. Loop through each country file
    for url in EPG_SOURCES:
        xml_bytes = fetch_url(url, "EPG File")
        if not xml_bytes: continue

        try:
            root = ET.fromstring(xml_bytes)
            
            # Map XML_ID -> Your_ID for this specific file
            file_id_map = {}

            for channel in root.findall('channel'):
                display_name = channel.find('display-name').text
                clean_name = normalize(display_name)
                
                # Check 1: Direct Match in your playlist
                your_id = my_channels.get(clean_name)
                
                # Check 2: Fuzzy Match
                if not your_id:
                    found = get_close_matches(clean_name, my_channels.keys(), n=1, cutoff=0.85)
                    if found:
                        your_id = my_channels[found[0]]
                
                # If matched, add to Master XML
                if your_id:
                    # Avoid duplicate channels
                    if your_id not in seen_ids:
                        file_id_map[channel.get('id')] = your_id
                        
                        channel.set('id', your_id)
                        master_root.append(channel)
                        seen_ids.add(your_id)
                        total_matches += 1

            # Add Programmes (Schedule) for matched channels
            for prog in root.findall('programme'):
                original_id = prog.get('channel')
                if original_id in file_id_map:
                    prog.set('channel', file_id_map[original_id])
                    master_root.append(prog)
                    
        except Exception as e:
            print(f"Error parsing XML from {url}: {e}")

    # 5. Save Final File
    tree = ET.ElementTree(master_root)
    tree.write(OUTPUT_FILENAME, encoding='UTF-8', xml_declaration=True)
    print(f"\nSUCCESS! Merged {total_matches} channels from FR, UK, ES, DE.")

if __name__ == "__main__":
    main()
