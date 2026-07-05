# Architecture

`visa_jobs_api` is a FastAPI service that, on trigger, runs two independent
job-scraping sources concurrently, merges and groups their results, and
emails the combined digest.

## Component diagram

```mermaid
flowchart TB
    subgraph API["api/ (HTTP layer)"]
        Router["routers/digest.py<br/>POST /digest/run, GET /health"]
        Service["services/digest_service.py<br/>orchestrates a run"]
        DTO["dto/digest.py<br/>DigestRunResponse, SourceCount"]
    end

    subgraph Aggregation["aggregation/aggregator.py"]
        Collect["collect_jobs()<br/>asyncio.gather both sources"]
        Group["group_and_sort_by_country()"]
        Render["render_digest_html()"]
    end

    subgraph Sources["sources/"]
        subgraph HN["hn_who_is_hiring/"]
            HNDiscover["discover.py"]
            HNFetch["fetch.py"]
            HNParse["parse.py"]
            HNExtract["extract.py<br/>regex + 24h window"]
            HNSource["source.py<br/>HnWhoIsHiringSource"]
        end
        subgraph LI["linkedin/"]
            LISearch["search.py<br/>adaptive pagination"]
            LIDesc["description.py"]
            LIExtract["extract.py<br/>dedup, title filter,<br/>country_from_location"]
            LISource["source.py<br/>LinkedInSource"]
        end
    end

    subgraph Shared["shared/ (used by both sources)"]
        Keywords["visa_keywords.py<br/>find_visa_mentions()"]
        LLM["visa_llm.py<br/>confirm_visa_offer()<br/>+ country inference"]
        Models["models.py<br/>NormalizedJob"]
        Concurrency["concurrency.py<br/>gather_limited() -- per-stage<br/>asyncio.Semaphore caps"]
        Http["http.py<br/>fetch_html() direct or<br/>via Bright Data Web Unlocker"]
    end

    Config["config.py<br/>Settings (.env)<br/>secrets + per-stage concurrency"]
    Emailer["emailer.py<br/>aiosmtplib -> Gmail SMTP"]

    Router --> Service
    Service --> DTO
    Service --> Collect
    Service --> Emailer
    Collect --> HNSource
    Collect --> LISource

    HNSource --> HNDiscover --> HNFetch --> HNParse --> HNExtract
    HNExtract --> Keywords
    HNSource --> LLM
    HNSource --> Concurrency

    LISource --> LISearch --> LIExtract
    LISource --> LIDesc
    LIExtract --> LIDesc
    LIDesc --> LIExtract
    LISource --> Keywords
    LISource --> LLM
    LISource --> Concurrency
    LISearch --> Http
    LIDesc --> Http

    HNSource --> Models
    LISource --> Models
    Collect --> Group --> Render

    Config -.-> HNSource
    Config -.-> LISource
    Config -.-> Http
    Config -.-> Emailer
```

## Request flow (a single `POST /digest/run`)

```mermaid
sequenceDiagram
    participant Client
    participant Router as api/routers/digest.py
    participant Service as api/services/digest_service.py
    participant Agg as aggregation/aggregator.py
    participant HN as HnWhoIsHiringSource
    participant LI as LinkedInSource
    participant Mail as emailer.py

    Client->>Router: POST /digest/run
    Router->>Service: run_digest(settings)
    Service->>Agg: collect_jobs([HN, LinkedIn])
    par both sources run concurrently
        Agg->>HN: fetch_jobs()
        HN->>HN: discover -> fetch -> parse -> extract (24h window)
        HN->>HN: LLM-confirm candidates (HN_LLM_CONCURRENCY)
        HN-->>Agg: list[NormalizedJob]
    and
        Agg->>LI: fetch_jobs()
        LI->>LI: search pages (LINKEDIN_SEARCH_CONCURRENCY,<br/>adaptive pagination)
        LI->>LI: dedupe, title-filter, 24h window
        LI->>LI: fetch descriptions (LINKEDIN_DESCRIPTION_CONCURRENCY)
        LI->>LI: LLM-confirm candidates (LINKEDIN_LLM_CONCURRENCY)
        LI-->>Agg: list[NormalizedJob]
    end
    Agg-->>Service: merged list[NormalizedJob]
    Service->>Agg: group_and_sort_by_country(jobs)
    Note over Agg: group by country (alphabetical),<br/>sort each country's jobs by posted_at desc,<br/>unresolved country -> "Remote / Unspecified"
    Agg-->>Service: grouped sections + rendered HTML
    Service->>Mail: send_success_email(subject, html)
    Mail-->>Client: (email delivered out-of-band)
    Service-->>Router: DigestRunResponse
    Router-->>Client: 200 OK, JSON summary

    alt any stage raises
        Service->>Mail: send_failure_email(reason)
        Service-->>Router: re-raises
        Router-->>Client: 502, error detail
    end
```
