"""Run evaluation questions through the SmartCat agent and record results.

Usage:
    python scripts/run_eval.py --db data/smartcat.db --output data/eval_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smartcat.agent.tools import AgentTools
from smartcat.agent.react_agent import ReactAgent
from smartcat.retrieval.hybrid_search import HybridSearcher
from smartcat.retrieval.reranker import Reranker
from smartcat.storage.sqlite_store import EmailStore
from smartcat.storage.qdrant_store import QdrantStore
from smartcat.embedding.embedder import Embedder

EVAL_QUESTIONS = [
    # Category 1: Factual Lookup
    "When did Enron file for Chapter 11 bankruptcy?",
    "Who sent the email about Pre-Bankruptcy Bonuses and what amount was mentioned?",
    "What was the total amount of retention bonuses paid before Enron's bankruptcy?",
    "Who is Jeff Dasovich and what was his role based on his emails?",
    "What is the Schedule Crawler and why did HourAhead failures occur?",
    "When did PG&E file for Chapter 11?",
    "Who sent emails about Demand Ken Lay Donate Proceeds from Enron Stock Sales?",
    "What was the subject of Ken Lay's email on November 30 2001?",
    "How many emails did Vince Kaminski send and what topics did he cover?",
    "What energy units like MMBtu and MWh appear most frequently in the corpus?",
    # Category 2: People & Relationships
    "Who were the most frequent email senders at Enron?",
    "Which people communicated most with Jeff Skilling?",
    "Who was Sara Shackleton and what department did she work in?",
    "What was Tana Jones responsible for based on her email patterns?",
    "Who were the key people involved in California energy discussions?",
    "Find emails between Kay Mann and external law firms.",
    "Who reported to Sally Beck based on email patterns?",
    "What was Chris Germany's area of responsibility?",
    "Which external parties non-Enron appear most in the correspondence?",
    "Find communications between Enron and government regulators.",
    # Category 3: Events & Timeline
    "What happened at Enron in October 2001?",
    "What were the key events in the California energy crisis as discussed in emails?",
    "When did employees first start discussing potential bankruptcy?",
    "What was the timeline of Enron stock price concerns in employee emails?",
    "Find emails about the Arthur Andersen document shredding.",
    "What happened with Enron Broadband Services?",
    "When did Ken Lay send his last company-wide email?",
    "What were the key milestones in the Enron-Dynegy merger discussions?",
    "Track the evolution of energy trading concerns from 2000 to 2001.",
    "When were employees told to report to work during the bankruptcy period?",
    # Category 4: Topics & Themes
    "What were the main legal concerns discussed in Enron emails?",
    "Find discussions about ISDA contracts and trading agreements.",
    "What natural gas trading strategies were discussed?",
    "Find emails about employee stock options and 401k plans.",
    "What compliance and regulatory issues were mentioned?",
    "Find discussions about Enron's West Coast power trading.",
    "What were the main HR-related topics in the email corpus?",
    "Find emails discussing deals worth more than 1 million dollars.",
    "What technology systems and IT issues were discussed?",
    "Find discussions about Enron's international operations.",
    # Category 5: Analysis & Reasoning
    "Based on the emails what warning signs existed before Enron's collapse?",
    "How did the tone of internal emails change from early 2001 to December 2001?",
    "What was the relationship between California energy crisis and Enron's trading?",
    "Which departments seemed most aware of financial irregularities?",
    "Compare the email patterns of executives vs regular employees during the crisis.",
    "What external companies were most exposed to Enron's collapse based on email mentions?",
    "Were there emails suggesting employees were told to hide information?",
    "What was the impact of the bankruptcy on Enron's trading operations?",
    "How did different departments react to the news of bankruptcy filing?",
    "Based on email evidence who were the key decision-makers in the final months?",
]

CATEGORIES = [
    "Factual Lookup", "Factual Lookup", "Factual Lookup", "Factual Lookup", "Factual Lookup",
    "Factual Lookup", "Factual Lookup", "Factual Lookup", "Factual Lookup", "Factual Lookup",
    "People", "People", "People", "People", "People",
    "People", "People", "People", "People", "People",
    "Timeline", "Timeline", "Timeline", "Timeline", "Timeline",
    "Timeline", "Timeline", "Timeline", "Timeline", "Timeline",
    "Topics", "Topics", "Topics", "Topics", "Topics",
    "Topics", "Topics", "Topics", "Topics", "Topics",
    "Reasoning", "Reasoning", "Reasoning", "Reasoning", "Reasoning",
    "Reasoning", "Reasoning", "Reasoning", "Reasoning", "Reasoning",
]


def main():
    parser = argparse.ArgumentParser(description="Run SmartCat evaluation")
    parser.add_argument("--db", default="data/smartcat.db")
    parser.add_argument("--output", default="data/eval_baseline.json")
    parser.add_argument("--start", type=int, default=0, help="Start from question N")
    parser.add_argument("--end", type=int, default=50, help="End at question N")
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    store.connect()
    qdrant = QdrantStore()
    embedder = Embedder(device="cpu")
    searcher = HybridSearcher(embedder, qdrant, store)
    reranker = Reranker(device="cpu")
    tools = AgentTools(searcher, reranker, store)
    agent = ReactAgent(tools, max_tokens=1500)

    # Load existing results if resuming
    results = []
    output_path = Path(args.output)
    if output_path.exists():
        with open(output_path) as f:
            results = json.load(f)
        # Auto-detect start from existing results
        if args.start == 0 and results:
            args.start = max(r["question_num"] for r in results)
            print(f"Resuming from question {args.start + 1} ({len(results)} already done)")

    questions = EVAL_QUESTIONS[args.start:args.end]
    categories = CATEGORIES[args.start:args.end]

    print(f"Running {len(questions)} questions ({args.start}-{args.end})...")
    print(f"Output: {args.output}\n")

    for i, (question, category) in enumerate(zip(questions, categories)):
        qnum = args.start + i + 1
        print(f"[{qnum}/50] ({category}) {question}")

        t0 = time.time()
        try:
            answer = agent.chat(question)
        except Exception as e:
            answer = f"ERROR: {e}"
        latency = time.time() - t0

        # Check if answer has citations (Message-ID, email_id, dates)
        has_citations = any(marker in answer for marker in [
            "Message-ID", "message_id", "ID:", "email_id",
            "@thyme", "@enron", "JavaMail",
        ])

        result = {
            "question_num": qnum,
            "category": category,
            "question": question,
            "answer": answer[:3000],  # cap for storage
            "latency_sec": round(latency, 1),
            "has_citations": has_citations,
            "answer_length": len(answer),
        }
        results.append(result)

        # Show brief summary
        preview = answer[:150].replace("\n", " ")
        print(f"  → {latency:.1f}s | citations={has_citations} | {preview}...")
        print()

        # Save after each question (resumable)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    # Print summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    total_latency = sum(r["latency_sec"] for r in results)
    cited = sum(1 for r in results if r["has_citations"])
    errors = sum(1 for r in results if r["answer"].startswith("ERROR"))
    print(f"Questions:  {len(results)}")
    print(f"Total time: {total_latency:.0f}s ({total_latency/60:.1f}m)")
    print(f"Avg latency: {total_latency/len(results):.1f}s")
    print(f"With citations: {cited}/{len(results)}")
    print(f"Errors: {errors}")

    # Per-category
    for cat in ["Factual Lookup", "People", "Timeline", "Topics", "Reasoning"]:
        cat_results = [r for r in results if r["category"] == cat]
        avg_lat = sum(r["latency_sec"] for r in cat_results) / max(len(cat_results), 1)
        cat_cited = sum(1 for r in cat_results if r["has_citations"])
        print(f"  {cat:15s}: avg {avg_lat:.1f}s, citations {cat_cited}/{len(cat_results)}")

    embedder.unload()
    store.close()
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
