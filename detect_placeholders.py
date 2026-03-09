import os
import re

OUTPUT_FILE = "detect_placeholders_output.txt"

# Patterns that indicate placeholder or fake configuration
PLACEHOLDER_PATTERNS = {
    "API_KEY_PLACEHOLDER": r"(your[_\-]?api[_\-]?key|replace[_\-]?me|dummy[_\-]?key|example[_\-]?key|test[_\-]?key)",
    "LOCALHOST_URL": r"(localhost|127\.0\.0\.1)",
    "EXAMPLE_DOMAIN": r"(example\.com|test-api|fake-endpoint)",
    "FAKE_DB_URL": r"(mongodb:\/\/localhost|postgres:\/\/.*localhost|sqlite:\/\/\/?.*)",
    "HUGGINGFACE_PLACEHOLDER": r"(hf_[a-zA-Z0-9]{10,})",
    "OPENAI_PLACEHOLDER": r"(sk-test|sk-placeholder|openai[_\-]?key)",
    "PLACEHOLDER_TEXT": r"(placeholder|changeme|todo|tbd)",
}

# File types to scan
SCAN_EXTENSIONS = [
    ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".env", ".txt", ".md", ".toml", ".ini", ".cfg",
    ".dockerfile", ".sh"
]

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build"
}

results = []


def should_scan_file(filename):
    return any(filename.endswith(ext) for ext in SCAN_EXTENSIONS)


def scan_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except:
        return

    for i, line in enumerate(lines, 1):
        for category, pattern in PLACEHOLDER_PATTERNS.items():
            if re.search(pattern, line, re.IGNORECASE):
                results.append({
                    "file": filepath,
                    "line": i,
                    "type": category,
                    "content": line.strip()
                })


def scan_workspace(root):
    for root_dir, dirs, files in os.walk(root):

        # remove excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for file in files:
            if should_scan_file(file):
                scan_file(os.path.join(root_dir, file))


def write_report():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:

        f.write("=== PLACEHOLDER DETECTION REPORT ===\n\n")

        if not results:
            f.write("No placeholders detected.\n")
            return

        for r in results:
            f.write(f"""
File: {r['file']}
Line: {r['line']}
Type: {r['type']}
Content: {r['content']}
-----------------------------------------
""")

        f.write(f"\nTotal placeholders found: {len(results)}\n")


if __name__ == "__main__":
    project_root = os.getcwd()
    scan_workspace(project_root)
    write_report()

    print("Scan complete.")
    print(f"Results saved to: {OUTPUT_FILE}")