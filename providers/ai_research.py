import asyncio
import logging
import httpx
from config import config
from providers.base import Quote

log = logging.getLogger(__name__)


async def generate_ai_research(quote: Quote) -> str:
    """Generate an AI-driven investor research briefing for a stock or crypto asset."""
    if not config.ai_api_key:
        return (
            "⚠️ <b>AI Key Not Configured</b>\n"
            "To enable `/research`, please add `AI_API_KEY` to your `.env` file."
        )

    prompt = _build_research_prompt(quote)
    provider = config.ai_provider

    try:
        if provider == "gemini":
            return await _call_gemini(prompt)
        elif provider == "openai":
            return await _call_openai(prompt)
        elif provider == "anthropic":
            return await _call_anthropic(prompt)
        else:
            # Fallback / Default to Gemini
            return await _call_gemini(prompt)
    except Exception as exc:
        log.error("AI Research generation error (%s): %s", provider, exc)
        return f"⚠️ <b>AI Research Error</b>: Could not generate research summary. ({exc})"


def _build_research_prompt(quote: Quote) -> str:
    market_cap_str = f"${quote.market_cap:,.0f}" if quote.market_cap else "N/A"
    vol_str = f"${quote.volume_24h:,.0f}" if quote.volume_24h else "N/A"
    
    extra_details = ""
    if quote.asset_type == "stock":
        pe_str = f"{quote.pe_ratio:.2f}" if quote.pe_ratio else "N/A"
        h52_str = f"${quote.fifty_two_week_high:,.2f}" if quote.fifty_two_week_high else "N/A"
        l52_str = f"${quote.fifty_two_week_low:,.2f}" if quote.fifty_two_week_low else "N/A"
        extra_details = f"- P/E Ratio: {pe_str}\n- 52-Week Range: {l52_str} - {h52_str}\n"

    return f"""You are an elite financial analyst and investment researcher.
Provide a concise, professional, bulleted research briefing for the following asset formatted in Telegram HTML (use <b>bold</b>, <i>italics</i>, <code>code</code>). Keep it under 350 words.

Asset Details:
- Name: {quote.name} ({quote.symbol})
- Type: {quote.asset_type.upper()}
- Price: ${quote.price:,.4f}
- 24h Change: {quote.change_24h_pct:+.2f}%
- Market Cap: {market_cap_str}
- 24h Volume: {vol_str}
{extra_details}

Include 4 clear sections:
1. <b>Executive Summary</b> (1-2 sentences on company/protocol role and market position)
2. <b>Key Drivers & Fundamentals</b> (Top 2-3 factors moving this asset)
3. <b>Technical & Price Action Context</b> (Current price relative to history/range)
4. <b>Investor Risk & Watchpoints</b> (Top 2 risk factors to monitor)

Do not include markdown headers (# or ##), use HTML bold tags (<b>Section Name</b>) instead.
"""


async def _call_gemini(prompt: str) -> str:
    # Model cascade list
    models_to_try = [config.ai_model] if config.ai_model else ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    last_exc = None
    async with httpx.AsyncClient(timeout=30) as client:
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.ai_api_key}"
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in (404, 429) and len(models_to_try) > 1 and model != models_to_try[-1]:
                    log.warning("Gemini model %s returned status %d. Trying next model...", model, resp.status_code)
                    await asyncio.sleep(1)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429:
                    log.warning("Gemini rate limit 429 hit for model %s", model)
                continue
            except Exception as exc:
                last_exc = exc
                continue

    if isinstance(last_exc, httpx.HTTPStatusError) and last_exc.response.status_code == 429:
        return (
            "⚠️ <b>Gemini Free Tier Quota Reached (429 Rate Limit)</b>\n"
            "Google AI Studio free tier limits the number of requests per minute/day.\n"
            "<i>Please wait 1 minute before requesting another AI research report!</i>"
        )
    raise last_exc or RuntimeError("All Gemini models failed")


async def _call_openai(prompt: str) -> str:
    model = config.ai_model or "gpt-4o-mini"
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {config.ai_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _call_anthropic(prompt: str) -> str:
    model = config.ai_model or "claude-3-5-sonnet-20241022"
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": config.ai_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
