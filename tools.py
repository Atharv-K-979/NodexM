from langchain.tools import tool 
import requests
from bs4 import BeautifulSoup
from tavily import TavilyClient
import os 
from dotenv import load_dotenv
from rich import print
load_dotenv()

tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

@tool
def web_search(query : str) -> str:
    """Search the web for recent and reliable information on a topic. Returns Titles, URLs and snippets."""
    try:
        results = tavily.search(query=query, max_results=5)
        out = []
        for r in results.get('results', []):
            out.append(
                f"Title: {r.get('title', 'No title')}\nURL: {r.get('url', '')}\nSnippet: {r.get('content', '')[:300]}\n"
            )
        return "\n----\n".join(out) if out else "No results found for query."
    except Exception as e:
        return f"Could not perform search: {str(e)}"

@tool
def scrape_url(url: str) -> str:
    """Scrape and return clean text content from a given URL for deeper reading."""
    try:
        url = url.strip().strip("'\"<>")
        if not url.startswith(("http://", "https://")):
            return f"Invalid URL: {url}"
        print(f"[bold cyan]Scraping URL:[/bold cyan] {url}")
        resp = requests.get(url, timeout=(4, 6), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:3000]
        return text if text else "No textual content could be parsed from the page."
    except Exception as e:
        return f"Could not scrape URL: {str(e)}"
    