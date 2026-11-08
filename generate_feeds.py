import os
import sys
import json
import ssl
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

BASE_URL = "https://bidduth79.github.io/bd-news-master-rss"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}

def fetch_url(url, timeout=5):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode('utf-8', errors='ignore')

def parse_rss_items(xml_str, source_name):
    items = []
    try:
        if '<?xml' in xml_str:
            xml_str = xml_str[xml_str.find('<?xml'):]
        elif '<rss' in xml_str:
            xml_str = xml_str[xml_str.find('<rss'):]
        root = ET.fromstring(xml_str)
        for item in root.findall('.//item')[:15]:
            title = item.findtext('title') or ''
            link = item.findtext('link') or ''
            pub_date = item.findtext('pubDate') or ''
            desc = item.findtext('description') or ''
            
            title = re.sub(r'<[^>]+>', '', title).strip()
            desc = re.sub(r'<[^>]+>', '', desc).strip()[:300]
            
            if title and link:
                items.append({
                    'title': title,
                    'raw_title': title,
                    'link': link.strip(),
                    'source': source_name,
                    'pubDate': pub_date or format_datetime(datetime.now(timezone.utc)),
                    'desc': desc or title
                })
    except Exception:
        pass
    return items

def scrape_homepage_articles(url, source_name):
    articles = []
    try:
        html = fetch_url(url, timeout=5)
        soup = BeautifulSoup(html, 'html.parser')
        seen = set()
        
        # Collect candidate anchor tags
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(' ', strip=True)
            # Must have Bengali characters and reasonable headline length
            if text and len(text) >= 16 and re.search(r'[\u0980-\u09FF]', text):
                # Exclude menu items, categories, tags, policies
                if any(x in href.lower() for x in ['tag/', 'category/', 'author/', 'archive/', 'privacy', 'about', 'contact', 'terms', 'signin', 'login']):
                    continue
                if any(x == text for x in ['হোম', 'বাংলাদেশ', 'আন্তর্জাতিক', 'খেলা', 'বিনোদন', 'অর্থনীতি', 'মতামত', 'লাইভ']):
                    continue
                
                full_url = urllib.parse.urljoin(url, href)
                if full_url not in seen:
                    seen.add(full_url)
                    clean_title = text.split('schedule')[0].strip()
                    articles.append({
                        'title': clean_title,
                        'raw_title': clean_title,
                        'link': full_url,
                        'source': source_name,
                        'pubDate': format_datetime(datetime.now(timezone.utc)),
                        'desc': clean_title
                    })
            if len(articles) >= 15:
                break
    except Exception:
        pass
    return articles

def process_single_source(source):
    s_id = source['id']
    s_name = source['name']
    s_domain = source['domain']
    items = []

    # 1. Try explicitly configured RSS if present
    if source.get('rss'):
        try:
            content = fetch_url(source['rss'], timeout=5)
            items = parse_rss_items(content, s_name)
        except Exception:
            pass

    # 2. If no items yet, try standard RSS paths
    if not items:
        standard_feeds = [
            f"https://{s_domain}/feed/",
            f"https://{s_domain}/rss.xml",
            f"http://{s_domain}/feed/",
            f"https://www.{s_domain}/feed/",
            f"https://{s_domain}/?feed=rss2"
        ]
        for feed_url in standard_feeds:
            try:
                content = fetch_url(feed_url, timeout=4)
                if 'xml' in content[:100].lower() or '<rss' in content[:200].lower():
                    parsed = parse_rss_items(content, s_name)
                    if parsed:
                        items = parsed
                        break
            except Exception:
                continue

    # 3. If still no items, run Universal Homepage Scraper
    if not items:
        for proto in ['https', 'http']:
            home_url = f"{proto}://{s_domain}"
            items = scrape_homepage_articles(home_url, s_name)
            if items:
                break

    return s_id, source, items

def build_feed_xml(title, link, desc, items, output_file):
    rss = ET.Element('rss', version='2.0', attrib={'xmlns:atom': 'http://www.w3.org/2005/Atom'})
    channel = ET.SubElement(rss, 'channel')

    ET.SubElement(channel, 'title').text = title
    ET.SubElement(channel, 'link').text = link
    ET.SubElement(channel, 'description').text = desc
    ET.SubElement(channel, 'language').text = 'bn'
    ET.SubElement(channel, 'lastBuildDate').text = format_datetime(datetime.now(timezone.utc))

    for art in items:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = art['title']
        ET.SubElement(item, 'link').text = art['link']
        ET.SubElement(item, 'guid', isPermaLink='true').text = art['link']
        ET.SubElement(item, 'pubDate').text = art.get('pubDate', format_datetime(datetime.now(timezone.utc)))
        ET.SubElement(item, 'description').text = art.get('desc', art['title'])
        if art.get('source'):
            ET.SubElement(item, 'source', url=art.get('link')).text = art['source']

    tree = ET.ElementTree(rss)
    ET.indent(tree, space='  ', level=0)
    tree.write(output_file, encoding='utf-8', xml_declaration=True)

def build_html_dashboard(root_dir, active_sources, total_items_count, total_sources_count):
    cards_html = ""
    for s in sorted(active_sources, key=lambda x: x['name']):
        s_id = s['id']
        feed_url = f"{BASE_URL}/feeds/{s_id}.xml"
        cards_html += f"""
        <div class="card" data-name="{s['name'].lower()} {s['domain'].lower()}">
            <div class="card-header">
                <h3>{s['name']}</h3>
                <span class="badge">সচল ({s['item_count']} খবর)</span>
            </div>
            <p class="site-link"><a href="{s['site']}" target="_blank">🌐 {s['domain']}</a></p>
            <div class="url-box">
                <input type="text" value="{feed_url}" readonly id="feed_{s_id}">
                <button onclick="copyToClipboard('feed_{s_id}', this)">কপি</button>
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>বাংলাদেশ সংবাদপত্র মাস্টার আরএসএস হাব (BD Newspapers Master RSS)</title>
    <link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Hind Siliguri', sans-serif; background: #f0f4f8; color: #1e293b; padding: 20px; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        header {{ text-align: center; margin-bottom: 25px; }}
        h1 {{ font-size: 32px; color: #0f172a; margin-bottom: 8px; }}
        p.subtitle {{ color: #475569; font-size: 18px; }}
        
        .stats-bar {{
            display: flex;
            gap: 15px;
            justify-content: center;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }}
        .stat-card {{
            background: white;
            padding: 12px 24px;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.03);
        }}
        .stat-num {{ font-size: 24px; font-weight: 700; color: #2563eb; }}
        .stat-label {{ font-size: 14px; color: #64748b; }}

        .master-box {{
            background: linear-gradient(135deg, #1e3a8a, #2563eb);
            color: white;
            padding: 25px;
            border-radius: 16px;
            box-shadow: 0 10px 25px -5px rgba(37, 99, 235, 0.4);
            margin-bottom: 30px;
        }}
        .master-box h2 {{ font-size: 24px; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
        .master-box p {{ font-size: 16px; opacity: 0.95; margin-bottom: 15px; }}
        
        .copy-group {{ display: flex; gap: 10px; }}
        .copy-group input {{
            flex: 1;
            padding: 12px 16px;
            border-radius: 8px;
            border: none;
            font-size: 16px;
            font-family: monospace;
            background: rgba(255, 255, 255, 0.95);
            color: #0f172a;
        }}
        .btn {{
            padding: 12px 24px;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            font-weight: 700;
            font-family: inherit;
            font-size: 16px;
            transition: all 0.2s;
        }}
        .btn-success {{ background: #10b981; color: white; }}
        .btn-success:hover {{ background: #059669; }}
        .btn-dark {{ background: #0f172a; color: white; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }}
        .btn-dark:hover {{ background: #1e293b; }}
        
        .action-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            margin-bottom: 25px;
        }}
        .search-box {{ flex: 1; max-width: 450px; }}
        .search-box input {{
            width: 100%;
            padding: 12px 18px;
            border-radius: 10px;
            border: 2px solid #cbd5e1;
            font-size: 16px;
            font-family: inherit;
        }}
        .search-box input:focus {{ border-color: #2563eb; outline: none; }}
        
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 15px;
        }}
        .card {{
            background: white;
            padding: 18px;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
        .card h3 {{ font-size: 18px; color: #1e293b; }}
        .badge {{ background: #dcfce7; color: #15803d; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 20px; }}
        .site-link {{ font-size: 13px; margin-bottom: 12px; }}
        .site-link a {{ color: #64748b; text-decoration: none; }}
        .site-link a:hover {{ text-decoration: underline; }}
        
        .url-box {{ display: flex; gap: 6px; }}
        .url-box input {{
            flex: 1;
            padding: 8px 10px;
            font-size: 13px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            background: #f8fafc;
            color: #475569;
            font-family: monospace;
        }}
        .url-box button {{
            padding: 8px 14px;
            border: none;
            background: #2563eb;
            color: white;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-family: inherit;
        }}
        .url-box button:hover {{ background: #1d4ed8; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🇧🇩 বাংলাদেশ সংবাদপত্র মাস্টার আরএসএস হাব</h1>
            <p class="subtitle">সকল জাতীয়, আঞ্চলিক ও স্থানীয় পত্রিকার সংবাদের কেন্দ্রীয় আরএসএস নেটওয়ার্ক</p>
        </header>

        <div class="stats-bar">
            <div class="stat-card">
                <div class="stat-num">{total_sources_count}</div>
                <div class="stat-label">মোট তালিকাভুক্ত পত্রিকা</div>
            </div>
            <div class="stat-card">
                <div class="stat-num">{len(active_sources)}</div>
                <div class="stat-label">সফল ও সচল ফিড</div>
            </div>
            <div class="stat-card">
                <div class="stat-num">{total_items_count}</div>
                <div class="stat-label">সর্বমোট সংগৃহীত তাজা খবর</div>
            </div>
        </div>

        <div class="master-box">
            <h2>★ কেন্দ্রীয় মাস্টার আরএসএস ফিড (Master RSS Feed)</h2>
            <p>এই একটি মাত্র লিংক Inoreader বা যেকোনো আরএসএস রিডারে সাবস্ক্রাইব করলে সবগুলো পত্রিকার সর্বশেষ সংবাদ একসাথে সরাসরি চলে আসবে।</p>
            <div class="copy-group">
                <input type="text" value="{BASE_URL}/master_rss.xml" id="master_url" readonly>
                <button class="btn btn-success" onclick="copyToClipboard('master_url', this)">মাস্টার লিংক কপি</button>
            </div>
        </div>

        <div class="action-bar">
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="🔍 পত্রিকা বা ডোমেইন সার্চ করুন..." onkeyup="filterCards()">
            </div>
            <div>
                <a href="{BASE_URL}/all_feeds.opml" download class="btn btn-dark">📥 ১-ক্লিকে Inoreader OPML ফাইল ডাউনলোড</a>
            </div>
        </div>

        <div class="grid" id="cardsGrid">
            {cards_html}
        </div>
    </div>

    <script>
        function copyToClipboard(elementId, btn) {{
            const input = document.getElementById(elementId);
            input.select();
            input.setSelectionRange(0, 99999);
            navigator.clipboard.writeText(input.value);
            const orig = btn.innerText;
            btn.innerText = 'কপি হয়েছে!';
            setTimeout(() => {{ btn.innerText = orig; }}, 2000);
        }}

        function filterCards() {{
            const query = document.getElementById('searchInput').value.toLowerCase();
            const cards = document.querySelectorAll('.card');
            cards.forEach(card => {{
                const data = card.getAttribute('data-name');
                if (data.includes(query)) {{
                    card.style.display = 'block';
                }} else {{
                    card.style.display = 'none';
                }}
            }});
        }}
    </script>
</body>
</html>
    """
    with open(os.path.join(root_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    sources_file = os.path.join(root_dir, 'sources.json')
    feeds_dir = os.path.join(root_dir, 'feeds')
    os.makedirs(feeds_dir, exist_ok=True)

    with open(sources_file, 'r', encoding='utf-8') as f:
        sources = json.load(f)

    print(f"Aggregating ALL {len(sources)} newspaper sources...")

    all_master_items = []
    active_sources = []

    # Run across ALL sources concurrently
    with ThreadPoolExecutor(max_workers=35) as executor:
        futures = {executor.submit(process_single_source, s): s for s in sources}
        completed_count = 0
        for future in as_completed(futures):
            completed_count += 1
            s_id, s_info, items = future.result()
            if items:
                s_info['item_count'] = len(items)
                active_sources.append(s_info)
                # Build individual feed
                feed_path = os.path.join(feeds_dir, f"{s_id}.xml")
                build_feed_xml(
                    title=f"{s_info['name']} - আরএসএস ফিড",
                    link=s_info['site'],
                    desc=f"{s_info['name']} এর সর্বশেষ খবর",
                    items=items,
                    output_file=feed_path
                )
                all_master_items.extend(items)
                if completed_count % 25 == 0 or len(items) > 0:
                    print(f"[{completed_count}/{len(sources)}] [OK] {s_info['name']}: {len(items)} items")

    # Deduplicate master items by link
    seen_links = set()
    deduped_master = []
    for item in all_master_items:
        if item['link'] not in seen_links:
            seen_links.add(item['link'])
            deduped_master.append(item)

    print(f"\nTotal active newspapers with news: {len(active_sources)}")
    print(f"Total deduplicated news articles: {len(deduped_master)}")
    
    # Write master_rss.xml with top 500 items
    master_path = os.path.join(root_dir, 'master_rss.xml')
    build_feed_xml(
        title="বাংলাদেশ সংবাদপত্র মাস্টার আরএসএস (All-in-One Master Feed)",
        link=BASE_URL,
        desc="সকল জাতীয় ও স্থানীয় পত্রিকার সকল সর্বশেষ খবরের সম্মিলিত মাস্টার আরএসএস ফিড",
        items=deduped_master[:500],
        output_file=master_path
    )
    print(f"[SUCCESS] master_rss.xml updated with {len(deduped_master[:500])} items!")

    # Build all_feeds.opml
    opml = ET.Element('opml', version='2.0')
    head = ET.SubElement(opml, 'head')
    ET.SubElement(head, 'title').text = 'Bangladesh Newspapers All Feeds'
    body = ET.SubElement(opml, 'body')

    folder = ET.SubElement(body, 'outline', text='বাংলাদেশ সকল সংবাদপত্র', title='বাংলাদেশ সকল সংবাদপত্র')
    ET.SubElement(folder, 'outline', 
                  type='rss', 
                  text='★ মাস্টার আরএসএস ফিড (সকল পত্রিকা এক সাথে)', 
                  title='★ মাস্টার আরএসএস ফিড (সকল পত্রিকা এক সাথে)', 
                  xmlUrl=f"{BASE_URL}/master_rss.xml", 
                  htmlUrl=BASE_URL)

    for s in active_sources:
        s_id = s['id']
        feed_url = f"{BASE_URL}/feeds/{s_id}.xml"
        ET.SubElement(folder, 'outline', 
                      type='rss', 
                      text=s['name'], 
                      title=s['name'], 
                      xmlUrl=feed_url, 
                      htmlUrl=s['site'])

    opml_tree = ET.ElementTree(opml)
    ET.indent(opml_tree, space='  ', level=0)
    opml_path = os.path.join(root_dir, 'all_feeds.opml')
    opml_tree.write(opml_path, encoding='utf-8', xml_declaration=True)
    print(f"[SUCCESS] all_feeds.opml generated with {len(active_sources) + 1} feeds!")

    # Build index.html
    build_html_dashboard(root_dir, active_sources, len(deduped_master), len(sources))
    print("[SUCCESS] index.html dashboard updated!")

if __name__ == '__main__':
    main()
