# UML model

This model describes the approved File Intelligence flow. It uses the standard
UML ideas from the referenced class-diagram and use-case lectures: classes own
data and operations, associations show collaboration, and the user must
explicitly approve every filesystem mutation.

## Class diagram

```mermaid
classDiagram
    class User {
        +approveRoot(path)
        +reviewContentSignals()
        +approveOrganizationIdea(planId, idea)
        +undo(planId)
    }

    class DashboardServer {
        +GET /api/files/insights
        +POST /api/files/sort/preview
        +POST /api/files/sort/approve-idea
        +POST /api/files/sort/undo
    }

    class FileService {
        +add_root(path, label)
        +get_insights()
        +generate_sort_plan(rootId)
        +approve_sort_idea(planId, idea)
        +undo_sort_plan(planId)
    }

    class FileStore {
        +roots
        +files
        +sort_plans
        +sort_suggestions
        +file_actions
    }

    class FileScanner {
        +walk(root)
        +extract_excerpt(file, maxBytes)
    }

    class FileInsight {
        +content_summary
        +content_inspected
        +review_required
        +review_reason
        +suggested_category
    }

    class SortAI {
        +build_suggestions(files, root)
    }

    class ApprovedRoot {
        +id
        +path
        +status
        +file_count
    }

    class FileRecord {
        +id
        +relative_path
        +extension
        +size_bytes
        +excerpt
    }

    class SortPlan {
        +id
        +status
        +expires_at
        +create_folders
    }

    class SortSuggestion {
        +source_relative
        +destination_relative
        +content_summary
        +review_required
        +review_reason
        +selected
        +status
    }

    class FileActionManager {
        +create_proposal(fileId, kind, destination)
        +apply_action(proposalId)
        +undo_action(actionId)
    }

    class FileAction {
        +source_relative
        +destination_relative
        +status
        +expected_identity
    }

    User --> DashboardServer : reviews through
    DashboardServer --> FileService : dispatches
    FileService --> FileStore : persists
    FileService --> FileScanner : starts scan
    FileService --> FileInsight : creates review data
    FileService --> SortAI : requests plan
    FileService --> FileActionManager : requests approved move
    ApprovedRoot "1" o-- "0..*" FileRecord : indexes
    SortPlan "1" *-- "1..*" SortSuggestion : contains
    SortSuggestion --> FileRecord : proposes for
    FileActionManager "1" --> "0..*" FileAction : journals
    SortSuggestion --> FileAction : records applied action
```

## Use-case diagram

```mermaid
flowchart LR
    User((User))
    K((Kshetrajna File Intelligence))

    ApproveRoot([Approve a local folder])
    Scan([Scan bounded metadata and allowed text previews])
    Inspect([Inspect local content profile])
    Review([Review content warning])
    OpenPreview([Open one-click file preview])
    Search([Search indexed files])
    Plan([Preview organization ideas])
    ApproveMove([Approve one folder/file idea])
    CreateFolder([Create destination folder])
    Move([Move file with journal])
    Quarantine([Review cleanup and quarantine file])
    Restore([Restore quarantined file])
    LegacyDelete([Legacy generic permanent DELETE API])
    Undo([Undo an eligible move])
    DeleteCatalog([Delete catalog data only])

    User --> ApproveRoot
    User --> Review
    User --> OpenPreview
    User --> Search
    User --> Plan
    User --> ApproveMove
    User --> Undo
    User --> Quarantine
    User --> Restore
    User --> LegacyDelete
    User --> DeleteCatalog

    ApproveRoot -. includes .-> Scan
    Scan -. includes .-> Inspect
    Inspect -. may produce .-> Review
    Review -. leads to .-> OpenPreview
    OpenPreview -. includes .-> Inspect
    OpenPreview -. ends with .-> Review
    OpenPreview -. may open .-> K
    ApproveMove -. includes .-> CreateFolder
    ApproveMove -. includes .-> Move
    Review -. may lead to .-> Quarantine
    Quarantine -. creates recovery record .-> Restore
    Move -. records .-> K
    Undo -. uses journal .-> K
    K --> Scan
    K --> Inspect
    K --> Plan
    K --> Move
    K --> Quarantine
    K --> Restore
    K --> LegacyDelete
```

## Content-review boundary

The `Inspect` use case reads at most 4 KiB from approved, non-sensitive text
extensions. It can describe likely source code, tabular data, documentation,
logs, empty files, placeholder names, draft markers, and generated artifacts.
Binary and media files are classified from metadata only. A `Review` result is
an explanation for the user, never an automatic delete or move command.
