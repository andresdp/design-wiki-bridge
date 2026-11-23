# Paper figures

Conceptual diagrams for the journal paper, drawn at the level of the method rather than the code. They were drafted before condition C (both routes) was added and before the test set was frozen: the paper also compares C, and the frozen sets have 10 public and 8–17 authored questions per repository (`benchmark-and-evaluation.md`). Figure 1 suits the introduction or the start of the method section, Figure 2 the method section (wiki search, section reads, span resolution, and citation assembly), and Figure 3 the benchmark and study design section. The implementation view is in [`architecture.md`](architecture.md).

Each figure is mermaid with a draft caption. To export a vector figure, paste the block into [mermaid.live](https://mermaid.live) and download SVG, or run `mmdc -i fig.mmd -o fig.pdf` with the mermaid CLI. The colors are placeholders. Redraw in the journal's style if needed, keeping the same elements.

## Figure 1. Conceptual overview

```mermaid
flowchart LR
    Q["Developer question<br/>+ allowed sources"]

    subgraph SRC["Evidence sources"]
        direction TB
        REPO[("Repository at a<br/>pinned commit<br/>code and tests")]
        WIKI[("Generated wiki<br/>OpenWiki")]
        PDF[("Documents<br/>specs, guides as PDF")]
        WEB[("Web<br/>replayed captures")]
    end

    subgraph DISC["Repository evidence discovery: the only difference"]
        direction TB
        A["A · Baseline<br/>vector search over code chunks"]
        B["B · Wiki-guided<br/>search wiki, read sections,<br/>resolve references to code"]
    end

    subgraph ANS["Shared answering, identical in A and B"]
        direction TB
        M["Merge candidates"] --> R["Rerank"] --> C["Token-budgeted context"] --> G["Generate claims<br/>citing evidence IDs"]
    end

    OUT["Answer with citations to<br/>original evidence<br/>commit, path, lines · document, page · URL, capture"]

    subgraph EVAL["Offline evaluation over saved traces"]
        direction TB
        RQ1["RQ1 evidence recall"]
        RQ2["RQ2 claim correctness,<br/>citation support and coverage"]
        RQ3["RQ3 tokens, latency,<br/>model usage, wiki cost"]
    end

    Q --> DISC
    REPO --> A
    WIKI -. guides discovery .-> B
    REPO --> B
    PDF --> M
    WEB -. only if the question allows it .-> M
    A --> M
    B --> M
    G --> OUT --> EVAL

    classDef var fill:#fde7c8,stroke:#c77700,color:#000
    classDef nav fill:#eeeeee,stroke:#888888,color:#000,stroke-dasharray:4 3
    class A,B var
    class WIKI nav
```

**Caption (draft).** Overview of the approach. A question is answered from original evidence: code and tests at a pinned commit, documents, and, when the question allows it, replayed web passages. The two conditions differ only in how repository evidence is discovered: vector search over code chunks (A) or wiki-guided discovery (B), where the generated wiki is used to navigate but is never cited. All later stages are shared, and evaluation runs offline over saved traces for the three research questions.

## Figure 2. Wiki-guided discovery: the wiki points, the code proves

```mermaid
flowchart LR
    Q["Question"] --> S["Wiki search<br/>ranked pages<br/>and sections"]
    S --> RD["Read selected<br/>sections"]
    RD --> REF["References in the section<br/>1 inline line links<br/>2 claims matched to the section<br/>3 page-level claims<br/>4 symbol and path names"]
    REF --> RES["Resolve at the<br/>pinned commit"]
    RES --> SPAN["Original code or<br/>test span<br/>path, lines 120–148"]
    SPAN --> E["Evidence item E3"]
    E --> CL["Claim ... E3"]

    RD -. section text kept in the trace .-> T[("Trace,<br/>never cited")]
    RES -. unresolved references logged .-> T

    classDef wiki fill:#eeeeee,stroke:#888888,color:#000
    classDef orig fill:#d9ecd0,stroke:#3c7a2a,color:#000
    class S,RD,REF wiki
    class SPAN,E,CL orig
```

**Caption (draft).** Wiki-guided discovery in condition B. Wiki search and section reads (grey) only locate candidate code: references are resolved in a fixed order, from exact line links to symbol and path names, into spans of the original files at the evaluated commit (green). The generator cites those spans, never wiki text, so citations in both conditions point to the same kind of evidence and are checked the same way.

## Figure 3. Study design

```mermaid
flowchart LR
    subgraph QS["Frozen question set, per repository"]
        direction TB
        P["Public SWE-QA items<br/>12–15, repository only"]
        X["Authored cross-source items<br/>6–8, code + document or web"]
        ANN["Annotations<br/>required claims,<br/>gold evidence spans"]
        P & X --> ANN
    end

    subgraph PIN["Pinned per repository: Flask, Requests"]
        direction TB
        SHA["Commit"]
        IDX["Code and PDF indexes"]
        WS["Wiki snapshot"]
        WR["Web capture store"]
    end

    MAN["Paired manifests<br/>same models, prompts,<br/>budgets, web policy"]
    RA["Run A"]
    RB["Run B"]
    TR[("Traces<br/>candidates, context,<br/>answer, usage")]
    SC["Scoring<br/>span matching,<br/>LLM judge, cost"]
    REP["Paired differences<br/>improved, tied, regressed<br/>by subset"]

    QS --> MAN
    PIN --> MAN
    MAN --> RA & RB
    RA & RB --> TR --> SC --> REP
    ANN --> SC
```

**Caption (draft).** Study design. For each repository, a frozen set of public and authored cross-source questions is answered by both conditions under paired manifests that fix the commit, indexes, wiki snapshot, web captures, models, prompts, and retrieval budgets. Saved traces are scored offline against annotated claims and evidence spans, and results are reported as paired differences for the public and custom subsets.

## Notes for the paper text

- **Name the controlled variable** wherever a figure appears: only repository discovery differs. The candidate budget for the repository branch is the one reported asymmetry (A retrieves 40 chunks, B up to 20 resolved spans); state it in the validity discussion.
- **Keep "never cited" visible** in Figure 1 or 2. It is the design choice that makes A and B attributions comparable.
- **Worked example.** The method outline asks for one. Figure 2 can be instantiated with a real trace: one question, the wiki section it read, the resolved span, and the final cited claim. Pick it from a dev question so the test set stays untouched.
