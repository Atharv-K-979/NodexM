import os
import json
from typing import Any

from langchain.agents import create_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import (
    StrOutputParser,
    JsonOutputParser,
)
from langchain_core.runnables import RunnableLambda

from tools import web_search, scrape_url
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
primary_model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

primary_llm = ChatGoogleGenerativeAI(
    model=primary_model,
    api_key=api_key,
    temperature=0.2,
)
fallback_models = ["gemini-3.1-flash-lite", "gemini-3.8-flash"]
fallbacks = [
    ChatGoogleGenerativeAI(model=m, api_key=api_key, temperature=0.2)
    for m in fallback_models
    if m != primary_model
]

llm = primary_llm.with_fallbacks(fallbacks)
json_llm = primary_llm.with_fallbacks(fallbacks).bind(response_format="application/json")


def build_search_agent():
    return create_agent(
        model=llm,
        tools=[web_search],
    )


def build_scraper_agent():
    return create_agent(
        model=llm,
        tools=[scrape_url],
    )


# ---------------------------------------------------------------------------
# 1. PLANNER: topic + profile -> 3-5 sub_questions with source-type hints
# ---------------------------------------------------------------------------
planner_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research planner. Break a research topic into 3 to 5 concrete, "
        "specific sub-questions that together cover the topic thoroughly. "
        "Avoid vague questions; each should be answerable by a web search. "
        "Tag each sub-question with a source-type hint from: "
        "general | academic | regulatory | news. "
        "Return ONLY valid JSON following the schema below with no prose around it.",
    ),
    (
        "human",
        """Topic: {topic}

User profile / preferences:
{user_profile}

Output JSON schema:
{{
  "sub_questions": [
    {{ "question": "...", "source_hint": "general|academic|regulatory|news" }}
  ]
}}""",
    ),
])

planner_chain = planner_prompt | json_llm | JsonOutputParser()


# ---------------------------------------------------------------------------
# 2. VERIFIER: all scraped sources -> agreed / disputed / single_source claims
# ---------------------------------------------------------------------------
verifier_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a claim verification analyst. You are given multiple sources with "
        "their scraped content (each source has an index, url, title, and text). "
        "Your job is to:\n"
        "  1. Extract every concrete, falsifiable factual claim each source makes. "
        "     Skip opinions, marketing fluff, and vague predictions.\n"
        "  2. Cross-check claims across all sources. Claims that 2+ sources agree on "
        "     go to 'agreed'. Claims that sources directly contradict go to "
        "     'disputed'. Claims that appear in only one source go to 'single_source'.\n"
        "  3. You MUST find at least some disputed or single-source claims when the "
        "     sources actually disagree or are sparse. NEVER return empty lists for "
        "     all three buckets — that means you failed.\n"
        "\nReturn ONLY valid JSON following the schema below, no other text.",
    ),
    (
        "human",
        """Research topic: {topic}

Sources (each source has `idx`, `url`, `title`, `text`):
{sources_json}

Output JSON schema — fill every bucket with real content, do not return empty lists unless absolutely unavoidable:
{{
  "agreed": [
    {{ "claim": "short concrete fact", "supporting_source_indices": [0, 2] }}
  ],
  "disputed": [
    {{
      "claim": "the fact under dispute",
      "conflicting_source_indices": [1, 3],
      "note": "Source 1 says X, Source 3 says Y — they directly contradict on..."
    }}
  ],
  "single_source": [
    {{ "claim": "short concrete fact", "source_index": 0 }}
  ]
}}""",
    ),
])

verifier_chain = verifier_prompt | json_llm | JsonOutputParser()


# ---------------------------------------------------------------------------
# 3. WRITER: topic + sources + verified claims -> markdown report
# ---------------------------------------------------------------------------
writer_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are an expert research writer. Write a clear, structured research report. "
        "CRITICAL RULE:\n"
        "  - Every disputed claim MUST be explicitly flagged with a DISPUTED marker "
        "    and a short note describing who says what. Never silently merge "
        "    conflicting facts.\n"
        "  - Single-source claims should be subtly hedged (e.g. 'According to X, ...') "
        "    so readers know the confidence level.\n"
        "  - Cite sources inline by index ([0], [1], ...).\n"
        "Write in professional, neutral prose.",
    ),
    (
        "human",
        """Topic: {topic}

User profile / preferences:
{user_profile}

Critique from previous revision (if any — address these issues):
{critique_text}

Sources (idx | url | title):
{sources_index}

Verified claims:
{verified_json}

Raw research notes (search snippets + extra context):
{research_notes}

Structure the report as:
# {topic}

## Executive Summary
(2-3 sentence high-level takeaway, call out any major disputes)

## Key Findings
(3-6 well-explained points; flag DISPUTED where sources disagree; inline citations [idx])

## Claims Requiring Further Review
(list disputed claims and single-source claims that matter most)

## Sources
(idx. Title — URL)

## Appendix: All Verified Claims
(bullet list of agreed, disputed, single_source with source indexes)

Return only the final markdown report, no surrounding commentary.""",
    ),
])

writer_chain = writer_prompt | llm | StrOutputParser()


# ---------------------------------------------------------------------------
# 4. CRITIC: report + context -> {score, issues, verdict}
# ---------------------------------------------------------------------------
critic_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a sharp research critic. Score the draft report on three axes, "
        "then produce a single integer overall score 1-10. Be honest and specific. "
        "Return ONLY valid JSON.",
    ),
    (
        "human",
        """Research topic: {topic}

Verified claims context (ground truth the report should align with):
{verified_json}

Draft report:
{report}

Output JSON schema:
{{
  "axes": {{
    "accuracy_vs_verifier": 10,
    "completeness": 10,
    "clarity": 10
  }},
  "score": 8,
  "issues": [
    "Specific actionable issue #1",
    "Specific actionable issue #2"
  ],
  "verdict": "APPROVE | REVISE"
}}""",
    ),
])

critic_chain = critic_prompt | json_llm | JsonOutputParser()


# ---------------------------------------------------------------------------
# 5. FORMATTER: final report + verified -> md / html / disputed callout
# ---------------------------------------------------------------------------
formatter_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a report formatter. Given a final markdown report and the verified "
        "claims JSON, produce three outputs. Return ONLY valid JSON.",
    ),
    (
        "human",
        """Final report markdown:
{report_md}

Verified claims:
{verified_json}

Output JSON schema:
{{
  "content_md": "...full markdown report as-is...",
  "content_html": "...the same report converted to clean semantic HTML with <h2>, <p>, <ul>, <strong> tags; wrap disputed callouts in <div class='disputed-callout' style='border-left:4px solid #ef4444;padding:12px;background:#fef2f2;'>...</div>; wrap single-source claims in <span class='single-source' style='color:#b45309'>...</span>...",
  "disputed_callout_md": "A separate short block summarizing ALL disputed claims in human-readable bullet form. Each bullet: claim + what each side says + source indexes.",
  "claims_summary": {{
    "agreed_count": 0,
    "disputed_count": 0,
    "single_source_count": 0
  }}
}}""",
    ),
])

formatter_chain = formatter_prompt | json_llm | JsonOutputParser()


# ---------------------------------------------------------------------------
# 6. SECTION REGENERATOR: re-write ONE section reusing cached verified context
# ---------------------------------------------------------------------------
section_regen_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research editor. Rewrite ONLY the requested section of the "
        "report, reusing the cached verified claims and sources. Do NOT make up "
        "new facts. Do not touch any other section. Return just the rewritten "
        "section as markdown with the same heading level.",
    ),
    (
        "human",
        """Topic: {topic}
Section name to rewrite: {section_name}
User instruction for rewrite: {user_instruction}

Sources (idx | url | title):
{sources_index}

Verified claims:
{verified_json}

Full current report (for context on surrounding tone):
{full_report_md}

Return ONLY the rewritten section markdown starting with its heading.""",
    ),
])

section_regen_chain = section_regen_prompt | llm | StrOutputParser()
