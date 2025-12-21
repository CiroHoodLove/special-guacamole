import urllib.request
import re
import xml.etree.ElementTree as ET
import sys
import gzip
import io
from difflib import get_close_matches

# ================= CONFIGURATION =================
# 1. Your Playlist
M3U_URL = "https://gist.githubusercontent.com/CiroHoodLove/ba36db853c30c47a3480020e87a352e6/raw/5e6e350293ec1c9ac7fa25fe1c0c6e5b3c3fabab/playlist.m3u"

# 2. THE CORRECT EPG SOURCE (iptv-epg.org)
# This matches the file you uploaded exactly.
EPG_URL = "https://iptv-epg.org/files/epg-fr.xml.gz"

OUTPUT_FILENAME = "custom_epg.xml"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
# =================================================

def fetch_url(url, description):
    print(f"Downloading {description}...")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req) as response:
            content = response.read()
            if url.endswith('.gz'):
                print("   -> Decompressing...")
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
                    return f.read()
            return content
    except Exception as e:
        print(f"!!! Error downloading {description}: {e}")
        return None

def smash_name(name):
    """
    The 'Nuclear' Cleaner.
    Removes ALL spaces, symbols, and special characters.
    FR - TF1   -> frtf1
    FR: TF1 HD -> frtf1
    """
    if not name: return ""
    name = name.lower()
    # Remove junk suffixes that might break the match
    junk = ['fhd', 'hd', 'hevc', 'h265', 'vip', '4k', 'backup', 'low', 'mobile', 'vod']
    for j in junk:
        name = name.replace(j, '')
    # Keep ONLY letters and numbers
    return re.sub(r'[^a-z0-9]', '', name)

def main():
    # 1. READ PLAYLIST
    m3u_bytes = fetch_url(M3U_URL, "Your Playlist")
    if not m3u_bytes: sys.exit(1)

    my_channels = {} # { smashed_name : tvg_id }
    
    print("Parsing Playlist...")
    for line in m3u_bytes.decode('utf-8', errors='ignore').splitlines():
        if line.startswith('#EXTINF'):
            # Extract TVG-ID
            id_match = re.search(r'tvg-id="([^"]+)"', line)
            
            # Extract Name (after the last comma)
            name_part = line.strip().split(',')[-1]
            
            if id_match and name_part:
                smashed = smash_name(name_part)
                my_channels[smashed] = id_match.group(1)

    print(f"✅ Loaded {len(my_channels)} channels from Playlist.")
    # Debug: Print a few to see what they look like
    print(f"   Sample Smashed Names: {list(my_channels.keys())[:5]}")

    # 2. READ EPG
    xml_bytes = fetch_url(EPG_URL, "EPG XML")
    if not xml_bytes: sys.exit(1)
    
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        print("!!! Critical: content is not valid XML.")
        sys.exit(1)

    # 3. MATCHING
    master_root = ET.Element("tv")
    master_root.set("generator-info-name", "Custom-Automator")
    
    matches = 0
    id_map = {} 
    
    print("Matching channels...")
    for channel in root.findall('channel'):
        xml_id = channel.get('id')
        display_name = channel.find('display-name').text
        
        # Clean the EPG name the same way
        smashed_xml = smash_name(display_name)
        
        # FIND MATCH
        # 1. Exact "Smashed" Match
        your_id = my_channels.get(smashed_xml)
        
        # 2. Fuzzy Match (If exact fails)
        if not your_id:
            found = get_close_matches(smashed_xml, my_channels.keys(), n=1, cutoff=0.85)
            if found:
                your_id = my_channels[found[0]]
        
        if your_id:
            # We found a match! Update ID and add to file.
            channel.set('id', your_id)
            master_root.append(channel)
            id_map[xml_id] = your_id
            matches += 1

    # 4. FIX PROGRAMMES
    for prog in root.findall('programme'):
        if prog.get('channel') in id_map:
            prog.set('channel', id_map[prog.get('channel')])
            master_root.append(prog)

    # 5. SAVE (ALWAYS SAVE!)
    tree = ET.ElementTree(master_root)
    tree.write(OUTPUT_FILENAME, encoding='UTF-8', xml_declaration=True)
    
    print(f"\n🚀 DONE! Matched {matches} channels.")
    if matches == 0:
        print("⚠️ WARNING: 0 Matches found. Check the Sample Names above to see why.")
    
    print(f"📁 Saved {OUTPUT_FILENAME}")

if __name__ == "__main__":
    main()
