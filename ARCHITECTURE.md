# Architecture

`visa_jobs_api` is a FastAPI service that, on trigger, runs three
independent job-scraping sources concurrently, merges and groups their
results, and emails the combined digest.

## Component diagram

```mermaid
flowchart TB
    subgraph API["api/ (HTTP layer)"]
        Router["routers/digest.py<br/>POST /digest/run, GET /health"]
        Service["services/digest_service.py<br/>orchestrates a run"]
        DTO["dto/digest.py<br/>DigestRunResponse, SourceCount"]
    end

    subgraph Aggregation["aggregation/aggregator.py"]
        Collect["collect_jobs()<br/>asyncio.gather all sources"]
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
            LISearch["search.py<br/>adaptive pagination,<br/>via Decodo"]
            LIDesc["description.py<br/>via Decodo"]
            LIQueries["queries.py<br/>14 countries"]
            LISource["source.py<br/>LinkedInSource"]
        end
        subgraph IN["indeed/"]
            INSearch["search.py<br/>adaptive pagination (nav-presence gate + vjk),<br/>via Selenium/headless Chrome"]
            INDesc["description.py<br/>via Bright Data"]
            INDomains["country_domains.py<br/>8 English-market countries"]
            INSource["source.py<br/>IndeedSource"]
        end
    end

    subgraph Shared["shared/ (used across sources)"]
        Keywords["visa_keywords.py<br/>find_visa_mentions()"]
        LLM["visa_llm.py<br/>confirm_visa_offer()<br/>+ country inference"]
        TitleFilter["title_filter.py<br/>dedup + title filter<br/>(LinkedIn + Indeed)"]
        Models["models.py<br/>NormalizedJob"]
        Concurrency["concurrency.py<br/>gather_limited() -- per-stage<br/>asyncio.Semaphore caps"]
        Http["http.py<br/>fetch_html() direct,<br/>via Decodo, or via Bright Data"]
        CallStats["call_stats.py<br/>per-country Bright Data/Decodo<br/>call counters, one shared instance<br/>per digest run"]
    end

    Config["config.py<br/>Settings (.env)<br/>secrets + per-stage concurrency"]
    Emailer["emailer.py<br/>aiosmtplib -> Gmail SMTP"]

    Router --> Service
    Service --> DTO
    Service --> Collect
    Service --> Emailer
    Service --> CallStats
    Collect --> HNSource
    Collect --> LISource
    Collect --> INSource

    HNSource --> HNDiscover --> HNFetch --> HNParse --> HNExtract
    HNExtract --> Keywords
    HNSource --> LLM
    HNSource --> Concurrency

    LISource --> LISearch --> TitleFilter
    LISource --> LIDesc
    LISource --> LIQueries
    LISource --> Keywords
    LISource --> LLM
    LISource --> Concurrency
    LISearch --> Http
    LIDesc --> Http
    LISearch --> CallStats
    LIDesc --> CallStats

    INSource --> INSearch --> TitleFilter
    INSource --> INDesc
    INSource --> INDomains
    INSource --> Keywords
    INSource --> LLM
    INSource --> Concurrency
    INSearch --> Http
    INDesc --> Http
    INSearch --> CallStats
    INDesc --> CallStats

    HNSource --> Models
    LISource --> Models
    INSource --> Models
    Collect --> Group --> Render

    Config -.-> HNSource
    Config -.-> LISource
    Config -.-> INSource
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
    participant IN as IndeedSource
    participant Stats as shared/call_stats.py
    participant Mail as emailer.py

    Client->>Router: POST /digest/run
    Router->>Service: run_digest(settings)
    Service->>Stats: new CallStats() (one shared instance for this run)
    Service->>Agg: collect_jobs([HN, LinkedIn, Indeed])
    par all three sources run concurrently
        Agg->>HN: fetch_jobs()
        HN->>HN: discover -> fetch -> parse -> extract (24h window)
        HN->>HN: LLM-confirm candidates (HN_LLM_CONCURRENCY)
        HN-->>Agg: list[NormalizedJob]
    and
        Agg->>LI: fetch_jobs()
        LI->>LI: search pages (LINKEDIN_SEARCH_CONCURRENCY,<br/>adaptive pagination, via Decodo)
        LI->>Stats: record_linkedin_search_call(country) per page
        LI->>LI: dedupe, title-filter, 24h window
        LI->>LI: fetch descriptions (LINKEDIN_DESCRIPTION_CONCURRENCY, via Decodo)
        LI->>Stats: record_linkedin_description_call(country) per fetch
        LI->>LI: LLM-confirm candidates (LINKEDIN_LLM_CONCURRENCY)
        LI-->>Agg: list[NormalizedJob]
    and
        Agg->>IN: fetch_jobs()
        IN->>IN: search pages (INDEED_SEARCH_CONCURRENCY,<br/>adaptive pagination via nav-presence gate + vjk,<br/>via Selenium/headless Chrome)
        IN->>Stats: record_indeed_search_call(country) per page
        IN->>IN: dedupe, title-filter (fromage already bounds recency)
        IN->>IN: fetch descriptions (INDEED_DESCRIPTION_CONCURRENCY, via Bright Data)
        IN->>Stats: record_indeed_description_call(country) per fetch
        IN->>IN: LLM-confirm candidates (INDEED_LLM_CONCURRENCY)
        IN-->>Agg: list[NormalizedJob]
    end
    Agg-->>Service: merged list[NormalizedJob]
    Service->>Stats: log_summary() -- per-country Bright Data/Decodo call breakdown<br/>(logged even if a source raised)
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
