Here is the complete Python script (update_epg.py) tailored to your specific requirements. It includes the normalization logic, the multi-assignment logic for your different stream qualities, and processes the specific list of countries you requested.

code
Python
download
content_copy
expand_less
import urllib.request
import gzip
import xml.etree.ElementTree as ET
import re
import io
import copy

# --- CONFIGURATION ---
M3U_URL = "https://gist.githubusercontent.com/CiroHoodLove/ba36db853c30c47a3480020e87a352e6/raw/5e6e350293ec1c9ac7fa25fe1c0c6e5b3c3fabab/playlist.m3u"

EPG_URLS = [
    "https://iptv-epg.org/files/epg-fr.xml.gz",
    "https://iptv-epg.org/files/epg-de.xml.gz",
    "https://iptv-epg.org/files/epg-uk.xml.gz",
    "https://iptv-epg.org/files/epg-es.xml.gz",
    "https://iptv-epg.org/files/epg-it.xml.gz",
    "https://iptv-epg.org/files/epg-us.xml.gz",
    "https://iptv-epg.org/files/epg-ca.xml.gz",
    "https://iptv-epg.org/files/epg-sa.xml.gz"
]

OUTPUT_FILENAME = "custom_epg.xml"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# --- NORMALIZATION LOGIC ---
def normalize_name(name):
    """
    Strips prefixes, suffixes, removes non-alphanumeric characters,
    and converts to lowercase to create a 'match key'.
    """
    if not name:
        return ""
    
    # 1. Define specific strings to remove (Case Insensitive)
    # Including the requested ones + standard country codes based on your file list
    remove_list = [
        # Prefixes
        r"^FR:", r"^UK \|", r"^DE -", r"^ES:", r"^IT:", r"^US:", r"^CA:", r"^SA:", 
        r"^FR\s", r"^UK\s", r"^DE\s", # Handle variations like "FR "
        # Suffixes (word boundaries \b ensure we don't delete 'HD' inside a word like 'HDTV')
        r"\bFHD\b", r"\bHD\b", r"\bSD\b", r"\bH265\b", r"\bVIP\b", r"\b4K\b", r"\bBackup\b",
        r"\bHEVC\b", r"\bAVC\b"
    ]
    
    clean_name = name
    
    for pattern in remove_list:
        clean_name = re.sub(pattern, "", clean_name, flags=re.IGNORECASE)

    # 2. Remove all non-alphanumeric characters (keep only A-Z, 0-9)
    clean_name = re.sub(r"[^a-zA-Z0-9]", "", clean_name)
    
    return clean_name.lower()

# --- NETWORK HELPERS ---
def download_url(url):
    print(f"Downloading: {url}")
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req) as response:
            return response.read()
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return None

# --- MAIN PROCESS ---
def main():
    # 1. Parse M3U Playlist to build the "Multi-Assign" Map
    # Map Structure: { 'normalized_name': ['id_100', 'id_101', 'id_102'] }
    m3u_content = download_url(M3U_URL)
    if not m3u_content:
        print("Failed to download M3U. Exiting.")
        return

    m3u_text = m3u_content.decode('utf-8', errors='ignore')
    m3u_map = {}
    
    print("Parsing M3U...")
    lines = m3u_text.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            # Extract tvg-id
            tvg_id_match = re.search(r'tvg-id="([^"]+)"', line)
            
            # Extract Channel Name (everything after the last comma)
            # This handles cases where commas exist inside the tags
            channel_name = line.split(',')[-1].strip()
            
            if tvg_id_match and channel_name:
                tvg_id = tvg_id_match.group(1)
                norm_name = normalize_name(channel_name)
                
                if norm_name:
                    if norm_name not in m3u_map:
                        m3u_map[norm_name] = []
                    # Avoid duplicates if M3U has same ID twice
                    if tvg_id not in m3u_map[norm_name]:
                        m3u_map[norm_name].append(tvg_id)

    print(f"Found {len(m3u_map)} unique channel names in M3U to look for.")

    # 2. Initialize Output XML
    output_root = ET.Element("tv")
    output_root.set("generator-info-name", "Custom EPG Generator")
    output_root.set("generator-info-url", "https://github.com/CiroHoodLove")

    # 3. Process EPGs
    processed_channels_count = 0
    
    for url in EPG_URLS:
        gz_data = download_url(url)
        if not gz_data:
            continue
            
        try:
            # Decompress GZIP
            with gzip.GzipFile(fileobj=io.BytesIO(gz_data)) as f:
                xml_content = f.read()
                
            # Parse XML
            tree = ET.fromstring(xml_content)
            
            # We need to map the SOURCE EPG ID to OUR DESTINATION IDs for this specific file
            # e.g. source_id "beinsports1.fr" -> maps to ["100", "101", "102"]
            source_to_dest_map = {}

            # Process Channels
            for channel in tree.findall("channel"):
                # Find display-name
                display_name_elem = channel.find("display-name")
                if display_name_elem is not None:
                    raw_name = display_name_elem.text
                    norm_name = normalize_name(raw_name)
                    
                    # Check if this EPG channel matches any channel in our M3U
                    if norm_name in m3u_map:
                        source_id = channel.get("id")
                        target_ids = m3u_map[norm_name]
                        
                        # Save mapping for processing programmes later
                        source_to_dest_map[source_id] = target_ids
                        
                        # DUPLICATE CHANNEL ENTRY FOR EACH TARGET ID
                        for tid in target_ids:
                            # Create a deep copy of the channel element
                            new_channel = copy.deepcopy(channel)
                            # Overwrite the ID with our M3U ID
                            new_channel.set("id", tid)
                            output_root.append(new_channel)
                            processed_channels_count += 1

            # Process Programmes
            for programme in tree.findall("programme"):
                src_channel = programme.get("channel")
                
                # If this program belongs to a channel we decided to keep
                if src_channel in source_to_dest_map:
                    target_ids = source_to_dest_map[src_channel]
                    
                    # DUPLICATE PROGRAMME ENTRY FOR EACH TARGET ID
                    for tid in target_ids:
                        new_prog = copy.deepcopy(programme)
                        new_prog.set("channel", tid)
                        output_root.append(new_prog)

            print(f"Processed {url} - Matched and merged channels.")

        except Exception as e:
            print(f"Failed to process XML from {url}: {e}")

    # 4. Write Final File
    print(f"Writing {OUTPUT_FILENAME} with {processed_channels_count} channel entries...")
    tree = ET.ElementTree(output_root)
    # xml_declaration=True and encoding='UTF-8' ensures headers are correct
    tree.write(OUTPUT_FILENAME, encoding="UTF-8", xml_declaration=True)
    print("Done.")

if __name__ == "__main__":
    main()
