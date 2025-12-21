import urllib.request
import gzip
import xml.etree.ElementTree as ET
import re
import io
import copy
import difflib

# --- CONFIGURATION ---

# 1. URL Sources
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
MISSING_REPORT_FILENAME = "missing_report.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# 2. Fuzzy Match Settings
FUZZY_MATCH_CUTOFF = 0.85  # 0.0 to 1.0 (0.85 = 85% similarity required)

# 3. MANUAL OVERRIDES
# Key: The normalized (smashed) name from your M3U.
# Value: The EXACT 'id' from the EPG XML source.
MANUAL_OVERRIDES = {
    # Example: "canalsport" : "CanalPlusSport.fr",
    # "beinsports1": "BeinSports1.fr" 
}

# --- NORMALIZATION LOGIC ---
def normalize_name(name):
    """
    Strips prefixes, suffixes, removes non-alphanumeric characters,
    and converts to lowercase to create a 'match key'.
    """
    if not name:
        return ""
    
    # Specific strings to remove (Case Insensitive)
    remove_list = [
        # Prefixes
        r"^FR:", r"^UK \|", r"^DE -", r"^ES:", r"^IT:", r"^US:", r"^CA:", r"^SA:", 
        r"^FR\s", r"^UK\s", r"^DE\s",
        # Suffixes
        r"\bFHD\b", r"\bHD\b", r"\bSD\b", r"\bH265\b", r"\bVIP\b", r"\b4K\b", r"\bBackup\b",
        r"\bHEVC\b", r"\bAVC\b"
    ]
    
    clean_name = name
    for pattern in remove_list:
        clean_name = re.sub(pattern, "", clean_name, flags=re.IGNORECASE)

    # Remove all non-alphanumeric characters (keep only A-Z, 0-9)
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
    # 1. Parse M3U Playlist
    # m3u_map structure: 
    # { 
    #   'normalized_name': {
    #       'ids': ['100', '101'], 
    #       'original_names': ['FR: Channel FHD', 'FR: Channel HD']
    #   } 
    # }
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
            tvg_id_match = re.search(r'tvg-id="([^"]+)"', line)
            channel_name = line.split(',')[-1].strip()
            
            if tvg_id_match and channel_name:
                tvg_id = tvg_id_match.group(1)
                norm_name = normalize_name(channel_name)
                
                if norm_name:
                    if norm_name not in m3u_map:
                        m3u_map[norm_name] = {'ids': [], 'original_names': []}
                    
                    if tvg_id not in m3u_map[norm_name]['ids']:
                        m3u_map[norm_name]['ids'].append(tvg_id)
                    
                    m3u_map[norm_name]['original_names'].append(channel_name)

    # Track which normalized names are still waiting for an EPG
    unmatched_keys = set(m3u_map.keys())
    total_m3u_channels = len(unmatched_keys)
    print(f"Found {total_m3u_channels} unique channel groups in M3U.")

    # 2. Initialize Output XML
    output_root = ET.Element("tv")
    output_root.set("generator-info-name", "Custom EPG Generator")
    output_root.set("generator-info-url", "https://github.com/CiroHoodLove")

    # 3. Process EPGs
    processed_channels_count = 0
    
    for url in EPG_URLS:
        if not unmatched_keys:
            print("All channels matched! Skipping remaining EPGs.")
            break

        gz_data = download_url(url)
        if not gz_data:
            continue
            
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(gz_data)) as f:
                xml_content = f.read()
                
            tree = ET.fromstring(xml_content)
            
            # Build EPG Indices for this file
            # epg_id_map: id -> element
            # epg_norm_name_map: normalized_name -> id
            epg_id_map = {}
            epg_norm_name_map = {}
            
            # Pre-scan EPG channels to build lookup tables
            for channel in tree.findall("channel"):
                c_id = channel.get("id")
                epg_id_map[c_id] = channel
                
                display_name = channel.find("display-name")
                if display_name is not None and display_name.text:
                    norm_epg_name = normalize_name(display_name.text)
                    # Store mapping. Note: Last channel with same name wins if duplicates exist in same EPG
                    epg_norm_name_map[norm_epg_name] = c_id

            # Mapping for this specific EPG file: source_id -> list_of_target_m3u_ids
            source_to_dest_map = {}
            
            # List of keys to remove from unmatched after this pass
            found_keys = []

            # MATCHING LOGIC
            for m3u_key in list(unmatched_keys): # Iterate copy so we can modify set
                match_found = False
                source_epg_id = None
                
                # A. MANUAL OVERRIDE CHECK
                if m3u_key in MANUAL_OVERRIDES:
                    override_id = MANUAL_OVERRIDES[m3u_key]
                    if override_id in epg_id_map:
                        source_epg_id = override_id
                        match_found = True
                        # print(f"  [Override] Matched {m3u_key} -> {source_epg_id}")

                # B. EXACT NAME MATCH
                if not match_found:
                    if m3u_key in epg_norm_name_map:
                        source_epg_id = epg_norm_name_map[m3u_key]
                        match_found = True
                        # print(f"  [Exact] Matched {m3u_key} -> {source_epg_id}")

                # C. FUZZY MATCH
                if not match_found:
                    # Get close matches from EPG keys
                    matches = difflib.get_close_matches(m3u_key, epg_norm_name_map.keys(), n=1, cutoff=FUZZY_MATCH_CUTOFF)
                    if matches:
                        best_match = matches[0]
                        source_epg_id = epg_norm_name_map[best_match]
                        match_found = True
                        # print(f"  [Fuzzy] Matched {m3u_key} (~ {best_match}) -> {source_epg_id}")

                # D. PROCESS MATCH
                if match_found and source_epg_id:
                    target_ids = m3u_map[m3u_key]['ids']
                    source_to_dest_map[source_epg_id] = target_ids
                    
                    # Add Channel Elements to Output
                    source_channel_elem = epg_id_map[source_epg_id]
                    for tid in target_ids:
                        new_channel = copy.deepcopy(source_channel_elem)
                        new_channel.set("id", tid)
                        output_root.append(new_channel)
                        processed_channels_count += 1
                    
                    found_keys.append(m3u_key)

            # Remove matched keys from the waitlist
            for k in found_keys:
                unmatched_keys.discard(k)

            print(f"  Matched {len(found_keys)} channels in this file.")

            # Copy Programmes
            # Only copy programmes if the channel was matched in this file
            prog_count = 0
            for programme in tree.findall("programme"):
                src_channel = programme.get("channel")
                if src_channel in source_to_dest_map:
                    target_ids = source_to_dest_map[src_channel]
                    for tid in target_ids:
                        new_prog = copy.deepcopy(programme)
                        new_prog.set("channel", tid)
                        output_root.append(new_prog)
                        prog_count += 1
            
            print(f"  Copied {prog_count} programme entries.")

        except Exception as e:
            print(f"Failed to process XML from {url}: {e}")

    # 4. Write Final XML
    print(f"\nWriting {OUTPUT_FILENAME}...")
    tree = ET.ElementTree(output_root)
    tree.write(OUTPUT_FILENAME, encoding="UTF-8", xml_declaration=True)

    # 5. Generate Missing Report
    print(f"Generating {MISSING_REPORT_FILENAME}...")
    with open(MISSING_REPORT_FILENAME, "w", encoding="utf-8") as f:
        f.write("--- MISSING CHANNELS REPORT ---\n")
        f.write(f"Total M3U Groups: {total_m3u_channels}\n")
        f.write(f"Matched Groups: {total_m3u_channels - len(unmatched_keys)}\n")
        f.write(f"Missing Groups: {len(unmatched_keys)}\n")
        f.write("-------------------------------\n\n")
        
        if len(unmatched_keys) == 0:
            f.write("CONGRATULATIONS! 100% MATCHED.\n")
        else:
            f.write("The following M3U channels (Original Names) could not be matched to any EPG:\n\n")
            # Sort for readability
            sorted_missing = sorted(list(unmatched_keys))
            for k in sorted_missing:
                original_names = m3u_map[k]['original_names']
                f.write(f"Key (Smashed): {k}\n")
                for name in original_names:
                    f.write(f" - {name}\n")
                f.write("\n")

    print("Done.")

if __name__ == "__main__":
    main()
