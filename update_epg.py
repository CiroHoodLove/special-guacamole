import urllib.request
import json
import re
import xml.etree.ElementTree as ET
from difflib import get_close_matches

# === CONFIGURATION ===
# Your IPTV Playlist
M3U_URL = "https://gist.githubusercontent.com/CiroHoodLove/ba36db853c30c47a3480020e87a352e6/raw/5e6e350293ec1c9ac7fa25fe1c0c6e5b3c3fabab/playlist.m3u"

# The Database & Source
API_CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
EPG_XML_URL = "https://iptv-org.github.io/epg/xml/fr.xml" # France Only
OUTPUT_FILENAME = "custom_epg.xml"
# =====================

def normalize(text):
    if not text: return ""
    text = text.lower()
    for word in ['fr', 'fr:', 'fr-', 'fhd', 'hd', 'hevc', 'vip', '4k', 'backup', 'low', 'mobile', '|', '★', '(', ')']:
        text = text.replace(word, '')
    return re.sub(r'[^a-z0-9]', '', text)

def main():
    print("1. Fetching Database...")
    with urllib.request.urlopen(API_CHANNELS_URL) as url:
        data = json.loads(url.read().decode())
    
    # Build Dictionary: { "tf1": "TF1.fr", "tf1fhd": "TF1.fr" }
    api_lookup = {}
    for item in data:
        if item.get('country') == 'FR': 
            cid = item.get('id')
            api_lookup[normalize(item.get('name'))] = cid
            for alt in item.get('alt_names', []):
                api_lookup[normalize(alt)] = cid

    print("2. Fetching Your Playlist...")
    my_channels = {} # { CleanName: Your_ID }
    with urllib.request.urlopen(M3U_URL) as response:
        for line in response.read().decode('utf-8', errors='ignore').splitlines():
            if line.startswith('#EXTINF'):
                tvg_match = re.search(r'tvg-id="([^"]+)"', line)
                name_part = line.strip().split(',')[-1]
                if tvg_match and name_part:
                    my_channels[normalize(name_part)] = tvg_match.group(1)

    print("3. Downloading EPG...")
    with urllib.request.urlopen(EPG_XML_URL) as response:
        tree = ET.parse(response)
        root = tree.getroot()

    print("4. Matching...")
    # Map XML_ID (TF1.fr) -> Your_ID (12345)
    xml_to_m3u = {} 
    
    # Reverse lookup: Use the XML Name -> Clean it -> Find in MyChannels
    for channel in root.findall('channel'):
        xml_id = channel.get('id')
        display_name = channel.find('display-name').text
        clean_name = normalize(display_name)
        
        # Try finding this name in your playlist
        your_id = my_channels.get(clean_name)
        
        # Fuzzy match fallback
        if not your_id:
            matches = get_close_matches(clean_name, my_channels.keys(), n=1, cutoff=0.85)
            if matches:
                your_id = my_channels[matches[0]]
        
        if your_id:
            channel.set('id', your_id)
            xml_to_m3u[xml_id] = your_id

    # Update Programmes
    for prog in root.findall('programme'):
        if prog.get('channel') in xml_to_m3u:
            prog.set('channel', xml_to_m3u[prog.get('channel')])

    tree.write(OUTPUT_FILENAME, encoding='UTF-8', xml_declaration=True)
    print("Done.")

if __name__ == "__main__":
    main()
