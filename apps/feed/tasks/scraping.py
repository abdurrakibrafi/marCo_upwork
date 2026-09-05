import re
import logging
from celery import shared_task
import requests

from apps.feed.models import FeedItem
from apps.feed.utils_url import resolve_real_article_url

logger = logging.getLogger(__name__)


def _clean_fallback_summary(content: str, title: str) -> str:
    """Generate a clean 1-2 paragraph preview summary from extracted article text.

    Args:
        content (str): Scraped markdown or plain text body.
        title (str): Article title for contextual sentence scoring.

    Returns:
        str: Formatted clean summary snippet.
    """
    if not content:
        return ""
    # Strip Jina headers if present
    if "Markdown Content:" in content:
        content = content.split("Markdown Content:", 1)[1]

    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

    # 1. Generate title keywords
    title_words = {w.lower() for w in re.findall(r'\w+', title) if len(w) > 3}

    # 2. Locate header matching title (start of actual body)
    body_start_index = 0
    for idx, p in enumerate(paragraphs):
        if p.startswith('#'):
            p_words = {w.lower() for w in re.findall(r'\w+', p) if len(w) > 3}
            intersection = title_words.intersection(p_words)
            if len(intersection) >= 2:
                body_start_index = idx
                break

    # 3. Find 1-2 paragraphs of actual content containing title keywords
    found_paragraphs = []
    for p in paragraphs[body_start_index:]:
        if p.startswith('#') or p.startswith('*') or p.startswith('-') or p.startswith('|') or p.startswith('['):
            continue

        # Clean markdown formatting and links
        plain = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', p)
        plain = re.sub(r'\!\[[^\]]*\]\([^\)]+\)', '', plain)
        plain = re.sub(r'\s+', ' ', plain).strip()

        lower_plain = plain.lower()
        # Skip sharing, credits, author bios, social media links
        if any(x in lower_plain for x in ('facebook', 'twitter', 'linkedin', 'share on', 'written by', 'editor for', 'read full bio', 'credit:')):
            continue

        if len(plain) > 80:
            plain_words = {w.lower() for w in re.findall(r'\w+', plain)}
            intersection = title_words.intersection(plain_words)
            if len(intersection) >= 2:
                found_paragraphs.append(plain)
                if len(found_paragraphs) >= 2:
                    break

    # Combine found paragraphs
    if found_paragraphs:
        return " ".join(found_paragraphs)

    # Absolute fallback: simple text cleaning on first 300 chars
    plain = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', content)
    plain = re.sub(r'\s+', ' ', plain).strip()
    if len(plain) > 300:
        short = plain[:300]
        last_dot = short.rfind('.')
        if last_dot > 100:
            return short[:last_dot + 1].strip()
        return short + '...'
    return plain


def _is_junk_page(url: str, content: str) -> bool:
    """Identify if extracted page text represents sportsbook marketing, terms, or non-article boilerplate.

    Args:
        url (str): Target web URL.
        content (str): Scraped body text.

    Returns:
        bool: True if identified as junk or promo page, else False.
    """
    if not content:
        return True

    content_len = len(content.strip())
    if content_len < 200:
        return True

    content_lower = content.lower()
    url_lower = url.lower()

    # 1. Sportsbook landing/promo domains
    sportsbook_domains = [
        "fanduel.com/sportsbook", "sportsbook.fanduel.com",
        "draftkings.com/sportsbook", "sportsbook.draftkings.com",
        "betmgm.com", "pointsbet.com", "caesars.com/sportsbook",
        "betrivers.com", "bet365.com"
    ]
    if any(domain in url_lower for domain in sportsbook_domains):
        return True

    # 2. Check for sign-up promotion landing pages (not news articles)
    promo_keywords = [
        "promo code", "bonus bet", "bonus bets", "sign-up bonus", "signup bonus",
        "risk-free bet", "deposit match", "sign-up offer", "signup offer",
        "gambling problem? call", "1-800-gambler", "must be 21+", "must be 21 or older",
        "terms and conditions apply", "wagering requirements", "wager $5", "bet $5",
        "new customers only", "bonus code", "free bet", "free bets", "terms apply",
        "gambling problem", "first deposit", "exclusive offer", "play now",
        "t&cs apply", "t&c apply", "wagering", "deposing", "deposit match",
        "odds", "spread", "moneyline", "parlay", "parlays", "fanduel", "draftkings",
        "betmgm", "caesars sportsbook", "bet365", "betrivers"
    ]
    promo_matches = sum(1 for kw in promo_keywords if kw in content_lower)

    if promo_matches >= 6:
        return True
    if promo_matches >= 3 and content_len < 2000:
        return True
    if any(x in url_lower for x in ["promo", "bonus", "betting", "odds", "wagering"]) and promo_matches >= 2:
        return True

    # 3. Standard boilerplate fallback detection
    boilerplate_indicators = [
        "cookie policy", "privacy policy", "terms of service", "terms of use",
        "all rights reserved", "contact us", "site map", "copyright", "feedback",
        "sign in", "create account", "forgot password", "log in"
    ]
    bp_matches = sum(1 for bp in boilerplate_indicators if bp in content_lower)
    if bp_matches >= 4 and content_len < 1000:
        return True

    return False


def extract_clean_article(html_or_markdown: str, url: str) -> str | None:
    """Extract clean news article body text by stripping headers, navigation, footers, and advertisement blocks.

    Employs a tiered pipeline: Trafilatura -> Readability-lxml -> BeautifulSoup fallback.

    Args:
        html_or_markdown (str): Raw HTML or Jina Markdown string.
        url (str): Source web page URL.

    Returns:
        str or None: Extracted readable body text, or None if extraction fails.
    """
    if not html_or_markdown:
        return None

    # Detect if the input is HTML
    is_html = (
        "<html>" in html_or_markdown or 
        "<body" in html_or_markdown or 
        "<div" in html_or_markdown or 
        "<p>" in html_or_markdown
    )

    if is_html:
        # Try 1: trafilatura
        try:
            import trafilatura
            extracted = trafilatura.extract(
                html_or_markdown,
                include_links=False,
                include_images=False,
                include_tables=False,
                no_fallback=False
            )
            if extracted and len(extracted.strip()) > 150:
                return extracted.strip()
        except ImportError:
            logger.info("trafilatura not installed, falling back to readability")
        except Exception as e:
            logger.warning(f"trafilatura extraction failed for {url}: {e}")

        # Try 2: readability-lxml
        try:
            from readability import Document
            from bs4 import BeautifulSoup
            doc = Document(html_or_markdown)
            summary_html = doc.summary()
            soup = BeautifulSoup(summary_html, "html.parser")

            # Decompose unwanted elements inside readability summary
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
                tag.decompose()

            extracted = soup.get_text(separator="\n").strip()
            lines = [line.strip() for line in extracted.split("\n") if line.strip()]
            extracted = "\n\n".join(lines)
            if extracted and len(extracted) > 150:
                return extracted
        except ImportError:
            logger.info("readability-lxml not installed, falling back to BeautifulSoup")
        except Exception as e:
            logger.warning(f"readability extraction failed for {url}: {e}")

        # Try 3: BeautifulSoup Fallback
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_or_markdown, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                tag.decompose()
            for element in soup.find_all(class_=re.compile(r'sportsbook|betting|promo|footer|nav|share|social', re.I)):
                element.decompose()
            for element in soup.find_all(id=re.compile(r'sportsbook|betting|promo|footer|nav|share|social', re.I)):
                element.decompose()

            paragraphs = [p.get_text().strip() for p in soup.find_all("p")]
            extracted = "\n\n".join([p for p in paragraphs if len(p) > 40])
            if extracted and len(extracted) > 150:
                return extracted
        except Exception as e:
            logger.warning(f"BeautifulSoup fallback extraction failed for {url}: {e}")
    else:
        # Markdown (e.g. from Jina Reader)
        lines = html_or_markdown.split("\n")
        cleaned_lines = []
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue
            line_lower = line_strip.lower()
            if any(term in line_lower for term in [
                "[terms & conditions]", "[privacy policy]", "[cookie policy]", "all rights reserved",
                "join fanduel", "promo code", "wager", "bonus bet", "sign-up bonus", "sportsbook promo",
                "click here to", "share this article", "follow us on", "read next", "related articles",
                "| contact us |", "bet $5 get", "bet $10 get", "fanduel sportsbook", "draftkings sportsbook"
            ]):
                continue
            cleaned_lines.append(line_strip)
        extracted = "\n\n".join(cleaned_lines)
        if len(extracted) > 150:
            return extracted

    return None


@shared_task(name='apps.feed.tasks.fetch_article_content', max_retries=2, default_retry_delay=10)
def fetch_article_content(feed_item_id: int):
    """Lazily fetch and parse full article body for a FeedItem using direct HTML scraping and Jina Reader fallback.

    Extracts text, strips ad/sportsbook clutter, and generates a preview summary.

    Args:
        feed_item_id (int): Primary key of the FeedItem.

    Returns:
        str: Extraction execution summary.
    """
    try:
        item = FeedItem.objects.get(id=feed_item_id)
    except FeedItem.DoesNotExist:
        return f"FeedItem {feed_item_id} not found"

    if item.content_fetched:
        return f"Already fetched for item {feed_item_id}"

    # ── Step 1: Decode Google redirect ──
    target_url = resolve_real_article_url(item.url)
    if target_url != item.url:
        logger.info(f"[Decoder] Successfully resolved redirect for item {feed_item_id} to: {target_url}")

    # ── Step 2: Fetch and Extract Content ──
    content = None
    fetched_html = None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        resp = requests.get(target_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            fetched_html = resp.text
            content = extract_clean_article(fetched_html, target_url)
    except Exception as exc:
        logger.info(f"[Scraper] Direct raw fetch failed for {target_url}: {exc}. Falling back to Jina.")

    if not content:
        jina_url = f"https://r.jina.ai/{target_url}"
        try:
            resp = requests.get(
                jina_url,
                headers={"Accept": "text/plain", "X-Timeout": "15"},
                timeout=20,
            )
            resp.raise_for_status()
            jina_content = resp.text.strip()
            content = extract_clean_article(jina_content, target_url)
        except Exception as exc:
            logger.warning(f"[Jina] Failed to fetch content for item {feed_item_id}: {exc}")
            item.content_fetched = False
            item.save(update_fields=["content_fetched"])
            return f"Extraction failed: direct and Jina fallback both failed ({exc})"

    if not content or len(content) < 200 or _is_junk_page(target_url, content):
        item.content = ""
        item.ai_summary = ""
        item.content_fetched = False
        item.save(update_fields=["content", "ai_summary", "content_fetched"])
        return f"Fetch skipped for item {feed_item_id}: junk page or content too short/empty"

    ai_summary = _clean_fallback_summary(content, item.title)

    item.content = content
    item.ai_summary = ai_summary
    item.content_fetched = True
    item.save(update_fields=["content", "ai_summary", "content_fetched"])

    logger.info(f"[Article] Fetched content + summary for FeedItem {feed_item_id}")
    return f"Done: item {feed_item_id}, content={len(content)} chars"
