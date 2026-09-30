from datetime import datetime, timezone

DEMO_ROOTS = [
    {"path": "C:\\Users\\Demo\\Projects\\webapp", "label": "Web Project"},
    {"path": "C:\\Users\\Demo\\Documents\\Research", "label": "Research Notes"},
]

DEMO_FILES = [
    {"root_idx": 0, "relative_path": "src/index.html", "name": "index.html", "extension": "html",
     "size_bytes": 4200, "is_directory": 0, "excerpt": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>WebApp</title>\n</head>"},
    {"root_idx": 0, "relative_path": "src/app.js", "name": "app.js", "extension": "js",
     "size_bytes": 8100, "is_directory": 0, "excerpt": "import { render } from './framework';\n\nconst App = () => {\n  return <div>Hello Web</div>;\n};"},
    {"root_idx": 0, "relative_path": "src/styles.css", "name": "styles.css", "extension": "css",
     "size_bytes": 3400, "is_directory": 0, "excerpt": ":root { --primary: #4a90d9; }\nbody { font-family: sans-serif; }"},
    {"root_idx": 0, "relative_path": "README.md", "name": "README.md", "extension": "md",
     "size_bytes": 2100, "is_directory": 0, "excerpt": "# My Web App\n\nA modern web application..."},
    {"root_idx": 0, "relative_path": "package.json", "name": "package.json", "extension": "json",
     "size_bytes": 890, "is_directory": 0, "excerpt": "{\n  \"name\": \"webapp\",\n  \"version\": \"1.0.0\"\n}"},
    {"root_idx": 0, "relative_path": "src/components/Header.jsx", "name": "Header.jsx", "extension": "jsx",
     "size_bytes": 1500, "is_directory": 0, "excerpt": "export function Header({ title }) {\n  return <header>{title}</header>;\n}"},
    {"root_idx": 0, "relative_path": "debug.log", "name": "debug.log", "extension": "log",
     "size_bytes": 12000, "is_directory": 0, "excerpt": "INFO: Server started\nDEBUG: Request received"},
    {"root_idx": 0, "relative_path": ".env", "name": ".env", "extension": "env",
     "size_bytes": 100, "is_directory": 0, "excerpt": ""},
    {"root_idx": 1, "relative_path": "neural-networks.md", "name": "neural-networks.md", "extension": "md",
     "size_bytes": 15000, "is_directory": 0, "excerpt": "# Neural Network Architectures\n\n## Overview\nThis document covers deep learning."},
    {"root_idx": 1, "relative_path": "datasets/summary.csv", "name": "summary.csv", "extension": "csv",
     "size_bytes": 42000, "is_directory": 0, "excerpt": "id,name,accuracy,parameters\n1,ResNet-50,76.1,25.6M\n2,VGG16,71.3,138M"},
    {"root_idx": 1, "relative_path": "document(7).txt", "name": "document(7).txt", "extension": "txt",
     "size_bytes": 2048, "is_directory": 0, "excerpt": "Qualcomm Benchmark Notes\n\nSnapdragon X Elite performance measurements."},
    {"root_idx": 1, "relative_path": "final_final_v3.md", "name": "final_final_v3.md", "extension": "md",
     "size_bytes": 4096, "is_directory": 0, "excerpt": "Kshetrajna Pitch Draft\n\nThe intelligence that knows the field."},
    {"root_idx": 1, "relative_path": "architecture_notes.txt", "name": "architecture_notes.txt", "extension": "txt",
     "size_bytes": 3500, "is_directory": 0, "excerpt": "Kshetrajna Architecture Notes\n\n- System Intelligence\n- File Intelligence"},
]

def seed_file_demo(store):
    now = datetime.now(timezone.utc).isoformat()
    
    with store._connect() as db:
        root_ids = []
        for r in DEMO_ROOTS:
            cursor = db.execute(
                "INSERT INTO roots (path, label, created_at, status) VALUES (?, ?, ?, 'ready')",
                (r["path"], r["label"], now)
            )
            root_id = cursor.lastrowid
            root_ids.append(root_id)
            db.execute("INSERT INTO scan_state (root_id, last_pass) VALUES (?, 1)", (root_id,))

        for f in DEMO_FILES:
            db.execute("""
                INSERT INTO files (root_id, relative_path, name, extension, size_bytes, modified_at, created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (root_ids[f["root_idx"]], f["relative_path"], f["name"], f["extension"], f["size_bytes"], now, now, f["is_directory"], "", f["excerpt"], now))

        for rid in root_ids:
            count = db.execute("SELECT COUNT(*) FROM files WHERE root_id=?", (rid,)).fetchone()[0]
            size = db.execute("SELECT SUM(size_bytes) FROM files WHERE root_id=?", (rid,)).fetchone()[0]
            db.execute("UPDATE roots SET file_count=?, total_bytes=?, last_scan=? WHERE id=?", (count, size, now, rid))
