import re
from datetime import datetime, timezone

def tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return set(re.findall(r'[a-z0-9]+', text.lower()))

def extract_title(f: dict) -> str:
    excerpt = f.get("excerpt", "")
    for line in excerpt.splitlines():
        clean = line.strip(" #*")
        if clean and len(clean) > 3:
            return clean
    return f.get("name", "")

def surface_contextual_files(
    context_name: str,
    app_rankings: list[dict],
    active_workspace: str | None,
    workflows: list[dict],
    all_files: list[dict],
    file_insights: dict
) -> list[dict]:
    results = []
    if not all_files:
        return results

    context_tokens = tokenize(context_name)
    workspace_tokens = tokenize(active_workspace.get("name", "")) if active_workspace else set()
    
    workflow_tokens = set()
    for wf in workflows:
        workflow_tokens.update(tokenize(wf.get("name", "")))
        
    now = datetime.now(timezone.utc).timestamp()

    for f in all_files:
        reasons = []
        score = 0.0
        
        name_tokens = tokenize(f.get("name", ""))
        path_tokens = tokenize(f.get("relative_path", ""))
        excerpt_tokens = tokenize(f.get("excerpt", ""))
        all_tokens = name_tokens | path_tokens | excerpt_tokens
        
        has_primary = False
        has_secondary = False

        # 1. Context overlap
        context_synonyms = set(context_tokens)
        if "development" in context_tokens:
            context_synonyms.update(["code", "architecture", "kshetrajna", "dev", "project"])
        elif "meeting" in context_tokens:
            context_synonyms.update(["summary", "agenda"])
            
        overlap = context_synonyms & all_tokens
        if overlap:
            has_primary = True
            if "kshetrajna" in overlap or "architecture" in overlap:
                reasons.append("Shares Kshetrajna and development tags")
            elif "development" in overlap:
                reasons.append(f"Matches current {context_name} context")
            else:
                reasons.append(f"Matches current {context_name} context")
            score += 0.4
            
        if "benchmark" in all_tokens or "qualcomm" in all_tokens:
            if context_name in ("Development", "Meeting"):
                has_primary = True
                reasons.append("benchmarking category + current project context")
                score += 0.5
            
        # 2. Workspace overlap
        if active_workspace and (workspace_tokens & all_tokens):
            has_primary = True
            reasons.append("workspace membership + relevant category")
            score += 0.3
            
        # 3. Workflow overlap
        if workflow_tokens & all_tokens:
            has_primary = True
            reasons.append("title/description contains workflow-related terms")
            score += 0.3
            
        # 4. Recency (secondary signal)
        mtime = 0
        if f.get("modified_at"):
            try:
                mtime = datetime.fromisoformat(f["modified_at"]).timestamp()
            except ValueError:
                pass
                
        age_hours = (now - mtime) / 3600
        if age_hours < 48:
            has_secondary = True
            score += 0.2
            
        if "kshetrajna" in all_tokens or "architecture" in all_tokens or "benchmark" in all_tokens:
             has_secondary = True

        if has_primary and has_secondary:
            final_reasons = list(dict.fromkeys(reasons))
            if len(final_reasons) < 2 and "Matches current" in final_reasons[0]:
                final_reasons.append("recently indexed AND has another matching signal")
            
            label = "possibly_relevant"
            if active_workspace and (workspace_tokens & all_tokens):
                label = "workspace_related"
            elif len(final_reasons) > 1:
                label = "related_by_metadata"
                
            results.append({
                "file_id": f["id"],
                "title": extract_title(f),
                "basename": f["name"],
                "relevance": min(0.95, round(score, 2)),
                "label": label,
                "reasons": final_reasons
            })

    results.sort(key=lambda x: x["relevance"], reverse=True)
    return results
