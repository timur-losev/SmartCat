"""SmartCat CLI — email search and AI chat interface."""

from __future__ import annotations

import click
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """SmartCat — RAG-powered email search with AI agent."""
    pass


@cli.command()
@click.option("--db", type=click.Path(), default="data/smartcat.db", help="SQLite database path")
def stats(db):
    """Show database statistics."""
    from smartcat.storage.sqlite_store import EmailStore

    store = EmailStore(Path(db))
    if not Path(db).exists():
        console.print("[red]Database not found. Run ingestion first.[/red]")
        return

    s = store.get_stats()
    table = Table(title="SmartCat Database Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Total emails", f"{s.get('total_emails', 0):,}")
    table.add_row("Unique senders", f"{s.get('unique_senders', 0):,}")
    table.add_row("With attachments", f"{s.get('with_attachments', 0):,}")
    table.add_row("Date range", f"{s.get('earliest', 'N/A')[:10]} → {s.get('latest', 'N/A')[:10]}")
    table.add_row("Avg email length", f"{s.get('avg_length', 0):.0f} chars")
    table.add_row("Total instances", f"{store.get_instance_count():,}")
    table.add_row("Participants", f"{store.get_participant_count():,}")
    table.add_row("Errors", f"{store.get_error_count():,}")

    console.print(table)
    store.close()


@cli.command()
@click.argument("query")
@click.option("--db", type=click.Path(), default="data/smartcat.db")
@click.option("--limit", default=10, help="Max results")
def search(query, db, limit):
    """Search emails using FTS5 keyword search (no vector DB required)."""
    from smartcat.storage.sqlite_store import EmailStore

    store = EmailStore(Path(db))

    results = store.search_fts(query, limit=limit)
    if not results:
        console.print(f"[yellow]No results for '{query}'[/yellow]")
        store.close()
        return

    console.print(f"\n[bold]Found {len(results)} results for '{query}':[/bold]\n")
    for i, r in enumerate(results, 1):
        date = (r.get("date_sent") or "N/A")[:10]
        subject = r.get("subject") or "(no subject)"
        sender = r.get("from_name") or r.get("from_address", "Unknown")
        body_preview = (r.get("body_text", "")[:120] + "...") if r.get("body_text") else ""

        console.print(f"[cyan]{i}.[/cyan] [{date}] [bold]{subject}[/bold]")
        console.print(f"   From: {sender}")
        console.print(f"   {body_preview}")
        console.print()

    store.close()


@cli.command()
@click.argument("name_or_email")
@click.option("--db", type=click.Path(), default="data/smartcat.db")
@click.option("--limit", default=20)
def participant(name_or_email, db, limit):
    """Search emails by participant name or email."""
    from smartcat.storage.sqlite_store import EmailStore

    store = EmailStore(Path(db))
    results = store.search_by_participant(name_or_email, limit=limit)

    if not results:
        console.print(f"[yellow]No emails found for '{name_or_email}'[/yellow]")
        store.close()
        return

    console.print(f"\n[bold]Found {len(results)} emails involving '{name_or_email}':[/bold]\n")
    for i, r in enumerate(results, 1):
        date = (r.get("date_sent") or "N/A")[:10]
        subject = r.get("subject") or "(no subject)"
        sender = r.get("from_name") or r.get("from_address", "Unknown")
        console.print(f"[cyan]{i}.[/cyan] [{date}] [bold]{subject}[/bold] — {sender}")

    store.close()


@cli.command()
@click.argument("message_id")
@click.option("--db", type=click.Path(), default="data/smartcat.db")
def email(message_id, db):
    """Show full email by Message-ID."""
    from smartcat.storage.sqlite_store import EmailStore

    store = EmailStore(Path(db))
    result = store.get_email(message_id)

    if not result:
        console.print(f"[red]Email not found: {message_id}[/red]")
        store.close()
        return

    console.print(f"\n[bold cyan]Message-ID:[/bold cyan] {result['message_id']}")
    console.print(f"[bold cyan]Date:[/bold cyan] {result.get('date_sent', 'N/A')}")
    console.print(f"[bold cyan]From:[/bold cyan] {result.get('from_name', '')} <{result.get('from_address', '')}>")
    console.print(f"[bold cyan]Subject:[/bold cyan] {result.get('subject', '(no subject)')}")
    console.print(f"[bold cyan]Thread:[/bold cyan] {result.get('thread_id', 'N/A')}")
    console.print(f"[bold cyan]Attachments:[/bold cyan] {'Yes' if result.get('has_attachments') else 'No'}")
    console.print("\n[bold]--- Body ---[/bold]")
    console.print(result.get("body_text", ""))

    store.close()


@cli.command()
@click.option("--db", type=click.Path(), default="data/smartcat.db")
def chat(db):
    """Interactive AI chat (requires llama-server running)."""
    from smartcat.storage.sqlite_store import EmailStore
    from smartcat.embedding.embedder import Embedder
    from smartcat.storage.qdrant_store import QdrantStore
    from smartcat.retrieval.hybrid_search import HybridSearcher
    from smartcat.retrieval.reranker import Reranker
    from smartcat.agent.tools import AgentTools
    from smartcat.agent.react_agent import ReactAgent

    store = EmailStore(Path(db))
    embedder = Embedder(device="cpu")  # CPU for query embedding in serving mode
    qdrant = QdrantStore()
    searcher = HybridSearcher(embedder, qdrant, store)
    reranker = Reranker()
    tools = AgentTools(searcher, reranker, store)
    agent = ReactAgent(tools)

    console.print("[bold green]SmartCat AI Chat[/bold green]")
    console.print("Type your question about the email corpus. Type 'quit' to exit.\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not query or query.lower() in ("quit", "exit", "q"):
            break

        console.print("\n[dim]Thinking...[/dim]")
        answer = agent.chat(
            query,
            stream_callback=lambda text: console.print(f"[dim]{text}[/dim]"),
        )
        console.print(f"\n[bold green]SmartCat:[/bold green] {answer}\n")

    store.close()
    console.print("\n[dim]Goodbye![/dim]")


if __name__ == "__main__":
    cli()
