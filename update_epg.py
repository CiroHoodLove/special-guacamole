import urllib.request
import gzip
import xml.etree.ElementTree as ET
import re
import io
import copy
import difflib
import concurrent.futures

# --- CONFIGURATION ---

M3U_URL = "https://gist.githubusercontent.com/CiroHoodLove/ba36db853c30c47a3480020e87a352e6/raw/5e6e350293ec1c9ac7fa25fe1c0c6e5b3c3fabab/playlist.m3u"

# Removed US as requested
EPG_URLS = [
    "https://iptv-epg.org/files/epg-fr.xml.gz",
    "https://iptv-epg.org/files/epg-de.xml.gz",
    "https://iptv-epg.org/files/epg-uk.xml.gz",
    "https://iptv-epg.org/files/epg-es.xml.gz",
    "https://iptv-epg.org/files/epg-it.xml.gz",
    "https://iptv-epg.org/files/epg-ca.xml.gz",
    "https://iptv-epg.org/files/epg-sa.xml.gz"
]

OUTPUT_FILENAME = "custom_epg.xml"
MISSING_REPORT_FILENAME = "missing_report.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
FUZZY_MATCH_CUTOFF = 0.85

MANUAL_OVERRIDES = {
    # "smashed_name": "ExactEPGID"
}

# --- PRE-COMPILED REGEX FOR SPEED ---
# Compiling regex once increases performance significantly inside loops
PREFIX_PATTERN = re.compile(r"^(FR:|UK \||DE -|ES:|IT:|US:|CA:|SA:|FR\s|UK\s|DE\s)", re.IGNORECASE)
SUFFIX_PATTERN = re.compile(r"\b(FHD|HD|SD|H265|VIP|4K|Backup|HEVC|AVC)\b", re.IGNORECASE)
NON_ALNUM_PATTERN = re.compile(r"[^a-zA-Z0-9]")

# --- HELPER FUNCTIONS ---

def normalize_name(name):
    if not name: return ""
    # Use compiled patterns
    clean = PREFIX_PATTERN.sub("", name)
    clean = SUFFIX_PATTERN.sub("", clean)
    clean = NON_ALNUM_PATTERN.sub("", clean)
    return clean.lower()

def download_url(url):
    """Downloads a URL and returns the bytes. Errors return None."""
    # print(f"Start DL: {url.split('/')[-1]}") 
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read()
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return None

def process_single_epg(url_data, unmatched_keys, m3u_map, outfile):
    """
    Processes one EPG binary blob.
    1. Decompresses in memory.
    2. Parses Channels to find matches (Exact -> Manual -> Fuzzy).
    3. Writes matched Channels to file immediately.
    4. Parses Programmes and writes matches immediately.
    Returns: A list of keys that were found (to update the global unmatched list).
    """
    url, gz_data = url_data
    if not gz_data:
        return []

    found_keys_in_this_file = []
    
    try:
        # Decompress into a seekable memory stream
        with gzip.GzipFile(fileobj=io.BytesIO(gz_data)) as f:
            # We read the whole XML into memory for seeking (channel pass + programme pass)
            # This is faster than re-downloading or re-unzipping
            xml_bytes = f.read()

        # Wrap in BytesIO for the parser
        context = io.BytesIO(xml_bytes)

        # --- PASS 1: SCAN CHANNELS & BUILD MAPS ---
        # We need to map EPG_ID -> [Target_M3U_IDs] for this file
        source_to_dest_map = {} # { 'epg_id': ['100', '101'] }
        
        # We store channel elements temporarily to perform fuzzy matching logic
        # structure: { 'norm_name': {'id': '...', 'elem': Element} }
        temp_channel_storage = {}
        
        # Iterparse is faster and uses less memory than tree parsing
        for event, elem in ET.iterparse(context, events=("end",)):
            if elem.tag == "channel":
                c_id = elem.get("id")
                display_name = elem.find("display-name")
                if display_name is not None and display_name.text:
                    n_name = normalize_name(display_name.text)
                    temp_channel_storage[n_name] = {'id': c_id, 'elem': elem}
                
                # Clear from memory to keep things fast, we stored what we needed
                # But we don't clear fully because we need the element structure later
                # actually, for small channel headers, keeping them in temp_channel_storage is fine.
                pass 
        
        # --- MATCHING LOGIC ---
        # Now we compare our M3U list against the channels found in this EPG
        
        epg_norm_names = list(temp_channel_storage.keys())
        
        for m3u_key in list(unmatched_keys):
            match_found = False
            source_epg_id = None
            
            # 1. Manual Override
            if m3u_key in MANUAL_OVERRIDES:
                # We have to find if this override ID exists in this specific file
                # This is tricky because we indexed by name, not ID. 
                # Let's do a quick lookup on values
                tgt_id = MANUAL_OVERRIDES[m3u_key]
                for k, v in temp_channel_storage.items():
                    if v['id'] == tgt_id:
                        source_epg_id = tgt_id
                        match_found = True
                        break

            # 2. Exact Match
            if not match_found:
                if m3u_key in temp_channel_storage:
                    source_epg_id = temp_channel_storage[m3u_key]['id']
                    match_found = True

            # 3. Fuzzy Match
            if not match_found:
                matches = difflib.get_close_matches(m3u_key, epg_norm_names, n=1, cutoff=FUZZY_MATCH_CUTOFF)
                if matches:
                    best_match = matches[0]
                    source_epg_id = temp_channel_storage[best_match]['id']
                    match_found = True

            # 4. If Matched, Prepare for Writing
            if match_found:
                # Retrieve the element
                # We need to find the element again. 
                # Optimization: We already have the element in temp_channel_storage if matched via name
                # If matched via Override (ID), we need the element.
                
                matched_elem = None
                
                # Find the element object
                for k, v in temp_channel_storage.items():
                    if v['id'] == source_epg_id:
                        matched_elem = v['elem']
                        break
                
                if matched_elem is not None:
                    target_ids = m3u_map[m3u_key]['ids']
                    source_to_dest_map[source_epg_id] = target_ids
                    
                    # WRITE CHANNELS IMMEDIATELY
                    for tid in target_ids:
                        # Modify ID
                        matched_elem.set("id", tid)
                        # Write string
                        xml_str = ET.tostring(matched_elem, encoding='utf-8').decode('utf-8')
                        outfile.write(xml_str + "\n")
                    
                    found_keys_in_this_file.append(m3u_key)

        # Free memory of channel storage
        del temp_channel_storage
        del epg_norm_names

        # --- PASS 2: PROCESS PROGRAMMES ---
        # Only if we found matches in this file
        if source_to_dest_map:
            context.seek(0) # Go back to start of XML
            # Use iterparse to stream programmes (very memory efficient)
            for event, elem in ET.iterparse(context, events=("end",)):
                if elem.tag == "programme":
                    src_channel = elem.get("channel")
                    
                    if src_channel in source_to_dest_map:
                        target_ids = source_to_dest_map[src_channel]
                        
                        # Write duplicates for every target ID
                        for tid in target_ids:
                            elem.set("channel", tid)
                            xml_str = ET.tostring(elem, encoding='utf-8').decode('utf-8')
                            outfile.write(xml_str + "\n")
                    
                    # CRITICAL: Clear element from memory after processing
                    elem.clear()

        print(f"  Processed {url.split('/')[-1]} - Matched: {len(found_keys_in_this_file)}")
        return found_keys_in_this_file

    except Exception as e:
        print(f"Error processing {url}: {e}")
        return []

# --- MAIN ---

def main():
    print("--- STARTING OPTIMIZED EPG GENERATOR ---")
    
    # 1. Download M3U
    m3u_content = download_url(M3U_URL)
    if not m3u_content: return

    m3u_text = m3u_content.decode('utf-8', errors='ignore')
    m3u_map = {}
    
    print("Parsing M3U...")
    # Optimized M3U Parsing
    for line in m3u_text.splitlines():
        if line.startswith("#EXTINF"):
            if 'tvg-id="' in line:
                tvg_id = line.split('tvg-id="')[1].split('"')[0]
                # Fast string split to get name (last part after comma)
                channel_name = line.rsplit(',', 1)[-1].strip()
                
                norm = normalize_name(channel_name)
                if norm:
                    if norm not in m3u_map:
                        m3u_map[norm] = {'ids': [], 'original_names': []}
                    if tvg_id not in m3u_map[norm]['ids']:
                        m3u_map[norm]['ids'].append(tvg_id)
                    m3u_map[norm]['original_names'].append(channel_name)

    unmatched_keys = set(m3u_map.keys())
    total_groups = len(unmatched_keys)
    print(f"M3U Parsed: {total_groups} unique channel groups.")

    # 2. Parallel Download of EPGs
    print("Downloading EPGs in parallel...")
    downloaded_epgs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all download tasks
        future_to_url = {executor.submit(download_url, url): url for url in EPG_URLS}
        
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                data = future.result()
                if data:
                    downloaded_epgs.append((url, data))
            except Exception as exc:
                print(f'{url} generated an exception: {exc}')

    # 3. Open Output File & Write Header
    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as outfile:
        outfile.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        outfile.write('<tv generator-info-name="Custom EPG" generator-info-url="github.com">\n')

        # 4. Process EPGs sequentially (but from memory) to write to file
        # We do this sequentially to avoid file write contention
        for epg_data in downloaded_epgs:
            if not unmatched_keys:
                print("All channels matched. Skipping remaining files.")
                break
                
            found = process_single_epg(epg_data, unmatched_keys, m3u_map, outfile)
            
            # Update unmatched list
            for k in found:
                unmatched_keys.discard(k)

        # Close XML Root
        outfile.write('</tv>')

    # 5. Report
    print(f"\nGenerating {MISSING_REPORT_FILENAME}...")
    with open(MISSING_REPORT_FILENAME, "w", encoding="utf-8") as f:
        f.write(f"Total Groups: {total_groups}\nMatched: {total_groups - len(unmatched_keys)}\nMissing: {len(unmatched_keys)}\n\n")
        if unmatched_keys:
            f.write("MISSING CHANNELS:\n")
            for k in sorted(list(unmatched_keys)):
                f.write(f"{k} : {m3u_map[k]['original_names'][0]}\n")
        else:
            f.write("PERFECT MATCH!\n")

    print("Done.")

if __name__ == "__main__":
    main()
