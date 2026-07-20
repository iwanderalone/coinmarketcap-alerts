import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List
import httpx
from config import config
from providers.base import Quote

log = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    link: str
    publisher: str
    published: str


async def fetch_news_items(symbol: str, asset_type: str = "stock") -> List[NewsItem]:
    """Fetch recent news headlines for a stock or crypto symbol."""
    items = []
    symbol_upper = symbol.strip().upper()

    if asset_type == "stock":
        try:
            items = await asyncio.to_thread(_fetch_yfinance_news_sync, symbol_upper)
        except Exception as exc:
            log.warning("yfinance news fetch failed for %s (%s). Falling back to Google News RSS...", symbol_upper, exc)

    if not items:
        items = await _fetch_google_news_rss(symbol_upper)

    return items[:5]


def _fetch_yfinance_news_sync(symbol: str) -> List[NewsItem]:
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    news_data = ticker.news or []
    items = []
    for item in news_data:
        # yfinance news object format handling
        content = item.get("content", {}) if isinstance(item.get("content"), dict) else item
        title = content.get("title") or item.get("title")
        link = content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else item.get("link")
        provider = content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else item.get("publisher", "Market News")
        pub_date = content.get("pubDate") or item.get("providerPublishTime", "")
        
        if title:
            items.append(NewsItem(
                title=title.strip(),
                link=link or f"https://finance.yahoo.com/quote/{symbol}",
                publisher=str(provider),
                published=str(pub_date)[:16],
            ))
    return items


async def _fetch_google_news_rss(symbol: str) -> List[NewsItem]:
    url = f"https://news.google.com/rss/search?q={symbol}+market+news&hl=en-US&gl=US&ceid=US:en"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        
    root = ET.fromstring(resp.text)
    items = []
    for item in root.findall("./channel/item")[:5]:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        source = item.findtext("source", "Google News")
        if title:
            items.append(NewsItem(
                title=title.strip(),
                link=link.strip(),
                publisher=source.strip(),
                published=pub_date[:16],
            ))
    return items


async def generate_ai_news_summary(quote: Quote, news_items: List[NewsItem]) -> str:
    """Generate an AI summary of news headlines using the configured AI key."""
    if not news_items:
        return f"📰 <b>No recent news headlines found for {quote.symbol}.</b>"

    headlines_str = "\n".join([f"- [{item.publisher}] {item.title}" for item in news_items])
    
    if not config.ai_api_key:
        # Fallback if no AI key configured: show clean bulleted headlines
        lines = [f"• <a href='{item.link}'>{item.title}</a> (<i>{item.publisher}</i>)" for item in news_items]
        return f"📰 <b>Latest News for {quote.name} ({quote.symbol})</b>:\n\n" + "\n".join(lines)

    prompt = f"""You are a financial news editor. Below are recent news headlines for {quote.name} ({quote.symbol}):

{headlines_str}

Summarize the key market sentiment, main story themes, and potential impact on {quote.symbol}'s price action in under 200 words. Format in Telegram HTML (use <b>bold</b>, <i>italics</i>). Include a bulleted list of 2-3 main takeaways. Do not use markdown headers (#).
"""
    try:
        from providers.ai_research import _call_gemini, _call_openai, _call_anthropic
        if config.ai_provider == "gemini":
            ai_summary = await _call_gemini(prompt)
        elif config.ai_provider == "openai":
            ai_summary = await _call_openai(prompt)
        elif config.ai_provider == "anthropic":
            ai_summary = await _call_anthropic(prompt)
        else:
            ai_summary = await _call_gemini(prompt)
            
        headlines_list = "\n".join([f"• <a href='{item.link}'>{item.title}</a> (<i>{item.publisher}</i>)" for item in news_items[:3]])
        return f"📰 <b>News Intelligence: {quote.symbol}</b>\n\n{ai_summary}\n\n<b>Top Headlines:</b>\n{headlines_list}"
    except Exception as exc:
        log.warning("AI news summary failed (%s), returning standard headlines...", exc)
        lines = [f"• <a href='{item.link}'>{item.title}</a> (<i>{item.publisher}</i>)" for item in news_items]
        return f"📰 <b>Latest News for {quote.name} ({quote.symbol})</b>:\n\n" + "\n".join(lines)
