import urllib.request
import json
import re
import xml.etree.ElementTree as ET
import sys
from difflib import get_close_matches

# === CONFIGURATION ===
M3U_URL = "https://gist.githubusercontent.com/CiroHoodLove/ba36db853c30c47a3480020e87a352e6/raw/5e6e350293ec1c9ac7fa25fe1c0c6e5b3c3fabab/playlist.m3u"
API_CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
EPG_XML_URL = "https://iptv-org.github.io/epg/xml/fr.xml"
OUTPUT_FILENAME = "custom_epg.xml"

# Headers to make us look like a real browser (Fixes 403 Forbidden)
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

def fetch_url(url, description):
    print(f"Downloading {description} from: {url}")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req) as response:
            return response.read()
    except Exception as e:
        print(f"!!! CRITICAL ERROR downloading {description}: {e}")
        # We exit with error code 1 to force the GitHub Action to show RED immediately
        sys.exit(1) 

def normalize(text):
    if not text: return ""
    text = text.lower()
    for word in ['fr', 'fr:', 'fr-', 'fhd', 'hd', 'hevc', 'vip', '4k', 'backup', '|', '(', ')']:
        text = text.replace(word, '')
    return re.sub(r'[^a-z0-9]', '', text)

def main():
    # 1. Fetch API
    data_bytes = fetch_url(API_CHANNELS_URL, "Channel Database")
    data = json.loads(data_bytes.decode())
    
    api_lookup = {}
    for item in data:
        if item.get('country') == 'FR': 
            cid = item.get('id')
            api_lookup[normalize(item.get('name'))] = cid
            for alt in item.get('alt_names', []):
                api_lookup[normalize(alt)] = cid

    # 2. Fetch M3U
    m3u_bytes = fetch_url(M3U_URL, "Your Playlist")
    my_channels = {}
    for line in m3u_bytes.decode('utf-8', errors='ignore').splitlines():
        if line.startswith('#EXTINF'):
            tvg_match = re.search(r'tvg-id="([^"]+)"', line)
            name_part = line.strip().split(',')[-1]
            if tvg_match and name_part:
                my_channels[normalize(name_part)] = tvg_match.group(1)

    print(f"Found {len(my_channels)} channels in your playlist.")

    # 3. Fetch EPG XML
    xml_bytes = fetch_url(EPG_XML_URL, "EPG XML")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"!!! Error parsing XML: {e}")
        sys.exit(1)

    # 4. Process
    print("Matching channels...")
    matches = 0
    xml_to_m3u = {} 

    for channel in root.findall('channel'):
        xml_id = channel.get('id')
        display_name = channel.find('display-name').text
        clean_name = normalize(display_name)
        
        your_id = my_channels.get(clean_name)
        if not your_id:
            found = get_close_matches(clean_name, my_channels.keys(), n=1, cutoff=0.85)
            if found:
                your_id = my_channels[found[0]]
        
        if your_id:
            channel.set('id', your_id)
            xml_to_m3u[xml_id] = your_id
            matches += 1

    # Update Programmes
    for prog in root.findall('programme'):
        if prog.get('channel') in xml_to_m3u:
            prog.set('channel', xml_to_m3u[prog.get('channel')])

    # Save
    tree = ET.ElementTree(root)
    tree.write(OUTPUT_FILENAME, encoding='UTF-8', xml_declaration=True)
    print(f"SUCCESS! Updated {matches} channels. File saved.")

if __name__ == "__main__":
    main()
