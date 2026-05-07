import streamlit as st
import json
import datetime
import sqlite3
import os
import hashlib
import base64
import ast
import re
import zipfile
import tempfile
import io
from pathlib import Path
from openai import OpenAI

# ═══════════════════════════════════════════════════════════════════
#  ADATBÁZIS RÉTEG
# ═══════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).parent / "code_optimizer.db"


def get_db() -> sqlite3.Connection:
    """Kapcsolat az SQLite adatbázishoz (WAL mód, foreign keys)."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Táblák létrehozása ha nem léteznek."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            code        TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            code_id             INTEGER NOT NULL REFERENCES codes(id) ON DELETE CASCADE,
            timestamp           TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            strategy            TEXT    NOT NULL,
            model               TEXT    NOT NULL,
            temperature         REAL    NOT NULL,
            goals               TEXT    NOT NULL,
            system_prompt       TEXT    NOT NULL,
            user_prompt         TEXT    NOT NULL,
            cot_prompt          TEXT,
            original_code       TEXT    NOT NULL,
            detected_language   TEXT,
            result_json         TEXT    NOT NULL,
            prompt_tokens       INTEGER,
            completion_tokens   INTEGER,
            total_tokens        INTEGER
        );
    """)
    conn.commit()
    conn.close()


# ── CRUD műveletek ──────────────────────────────────────────────────

def db_save_code(name: str, code: str) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO codes (name, code) VALUES (?, ?)",
        (name, code),
    )
    code_id = cur.lastrowid
    conn.commit()
    conn.close()
    return code_id


def db_update_code(code_id: int, name: str, code: str):
    conn = get_db()
    conn.execute(
        "UPDATE codes SET name = ?, code = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (name, code, code_id),
    )
    conn.commit()
    conn.close()


def db_delete_code(code_id: int):
    conn = get_db()
    conn.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()


def db_list_codes() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT c.id, c.name, c.code, c.created_at, c.updated_at,
                  COUNT(r.id) as run_count
           FROM codes c LEFT JOIN runs r ON r.code_id = c.id
           GROUP BY c.id ORDER BY c.updated_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_get_code(code_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def db_save_run(code_id: int, run_data: dict) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO runs
           (code_id, strategy, model, temperature, goals,
            system_prompt, user_prompt, cot_prompt, original_code,
            detected_language, result_json,
            prompt_tokens, completion_tokens, total_tokens)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            code_id,
            run_data["strategy"],
            run_data["model"],
            run_data["temperature"],
            json.dumps(run_data["goals"], ensure_ascii=False),
            run_data["system_prompt"],
            run_data["user_prompt"],
            run_data.get("cot_prompt"),
            run_data["original_code"],
            run_data.get("detected_language"),
            json.dumps(run_data["result"], ensure_ascii=False),
            run_data["usage"]["prompt_tokens"],
            run_data["usage"]["completion_tokens"],
            run_data["usage"]["total_tokens"],
        ),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def db_get_runs(code_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM runs WHERE code_id = ? ORDER BY timestamp ASC",
        (code_id,),
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d["goals"] = json.loads(d["goals"])
        d["result"] = json.loads(d["result_json"])
        d["usage"] = {
            "prompt_tokens": d["prompt_tokens"],
            "completion_tokens": d["completion_tokens"],
            "total_tokens": d["total_tokens"],
        }
        results.append(d)
    return results


def db_export_code_with_runs(code_id: int) -> dict:
    code = db_get_code(code_id)
    runs = db_get_runs(code_id)
    # Tisztítjuk a felesleges mezőket az exportból
    clean_runs = []
    for r in runs:
        clean_runs.append({
            "timestamp": r["timestamp"],
            "strategy": r["strategy"],
            "model": r["model"],
            "temperature": r["temperature"],
            "goals": r["goals"],
            "system_prompt": r["system_prompt"],
            "user_prompt": r["user_prompt"],
            "cot_prompt": r.get("cot_prompt"),
            "detected_language": r.get("detected_language"),
            "result": r["result"],
            "usage": r["usage"],
        })
    return {
        "code_name": code["name"],
        "original_code": code["code"],
        "exported_at": datetime.datetime.now().isoformat(),
        "runs": clean_runs,
    }


# ═══════════════════════════════════════════════════════════════════
#  API KULCS BIZTONSÁGOS KEZELÉSE
# ═══════════════════════════════════════════════════════════════════

ENV_FILE = Path(__file__).parent / ".env"
KEY_SALT = b"code_optimizer_v1"


def _derive_key(password: str) -> bytes:
    """PBKDF2 kulcs származtatás a jelszóból."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), KEY_SALT, 100_000)


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    """Egyszerű XOR titkosítás/visszafejtés."""
    return bytes(d ^ key[i % len(key)] for i, d in enumerate(data))


def save_api_key_encrypted(api_key: str, password: str):
    """API kulcs mentése titkosítva a .env fájlba."""
    key = _derive_key(password)
    encrypted = _xor_bytes(api_key.encode(), key)
    encoded = base64.b64encode(encrypted).decode()

    lines = []
    if ENV_FILE.exists():
        lines = [l for l in ENV_FILE.read_text().splitlines() if not l.startswith("OPENAI_API_KEY_ENC=")]
    lines.append(f"OPENAI_API_KEY_ENC={encoded}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def load_api_key_encrypted(password: str) -> str | None:
    """API kulcs visszafejtése a .env fájlból."""
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("OPENAI_API_KEY_ENC="):
            encoded = line.split("=", 1)[1].strip()
            key = _derive_key(password)
            encrypted = base64.b64decode(encoded)
            return _xor_bytes(encrypted, key).decode()
    return None


def has_saved_key() -> bool:
    """Van-e mentett titkosított kulcs?"""
    if not ENV_FILE.exists():
        return False
    return any(l.startswith("OPENAI_API_KEY_ENC=") for l in ENV_FILE.read_text().splitlines())


def delete_saved_key():
    """Mentett kulcs törlése."""
    if ENV_FILE.exists():
        lines = [l for l in ENV_FILE.read_text().splitlines() if not l.startswith("OPENAI_API_KEY_ENC=")]
        ENV_FILE.write_text("\n".join(lines) + "\n")


def get_api_key_from_env() -> str | None:
    """Környezeti változóból vagy .env fájlból (titkosítatlanul)."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY=") and not line.startswith("OPENAI_API_KEY_ENC"):
                return line.split("=", 1)[1].strip()
    return None


# ═══════════════════════════════════════════════════════════════════
#  KÓDBÁZIS ELEMZÉS — Segédfüggvények
# ═══════════════════════════════════════════════════════════════════

SUPPORTED_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".java": "Java",
    ".cpp": "C++",
    ".c": "C",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
}


def extract_zip(uploaded_file) -> dict[str, str]:
    """ZIP fájl kicsomagolása — visszaadja a {relatív_útvonal: tartalom} dict-et."""
    files = {}
    with zipfile.ZipFile(io.BytesIO(uploaded_file.getvalue()), "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            ext = os.path.splitext(info.filename)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            # Kihagyjuk a rejtett fájlokat, node_modules, __pycache__, .git stb.
            parts = Path(info.filename).parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".git", "dist", "build") for p in parts):
                continue
            try:
                content = zf.read(info.filename).decode("utf-8", errors="replace")
                files[info.filename] = content
            except Exception:
                pass
    return files


def parse_python_file(filepath: str, content: str) -> list[dict]:
    """Python fájl AST elemzése — függvények és osztályok kinyerése."""
    units = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return units

    lines = content.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else start + 1
            code = "\n".join(lines[start:end])
            # Dekorátorok hozzáadása
            if node.decorator_list:
                dec_start = node.decorator_list[0].lineno - 1
                code = "\n".join(lines[dec_start:end])

            args = [a.arg for a in node.args.args]
            units.append({
                "type": "function",
                "name": node.name,
                "file": filepath,
                "line_start": start + 1,
                "line_end": end,
                "args": args,
                "lines": end - start,
                "code": code,
                "language": "Python",
            })

        elif isinstance(node, ast.ClassDef):
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else start + 1
            code = "\n".join(lines[start:end])
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            units.append({
                "type": "class",
                "name": node.name,
                "file": filepath,
                "line_start": start + 1,
                "line_end": end,
                "args": [],
                "methods": methods,
                "lines": end - start,
                "code": code,
                "language": "Python",
            })

    return units


def parse_js_ts_file(filepath: str, content: str, language: str) -> list[dict]:
    """JavaScript/TypeScript fájl regex-alapú elemzése."""
    units = []
    lines = content.splitlines()

    # Patterns: function declarations, arrow functions, class declarations, methods
    patterns = [
        # function name(...) {
        (r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\([^)]*\)', "function"),
        # const/let/var name = (...) => { vagy function(
        (r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>', "function"),
        # class Name {
        (r'(?:export\s+)?class\s+(\w+)', "class"),
    ]

    for pattern, unit_type in patterns:
        for match in re.finditer(pattern, content):
            name = match.group(1)
            pos = match.start()
            line_num = content[:pos].count("\n")

            # Egyszerű brace-counting a blokk végéig
            block_start = content.find("{", pos)
            if block_start == -1:
                continue

            depth = 0
            end_pos = block_start
            for i in range(block_start, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end_pos = i + 1
                        break

            end_line = content[:end_pos].count("\n")
            code = "\n".join(lines[line_num:end_line + 1])

            units.append({
                "type": unit_type,
                "name": name,
                "file": filepath,
                "line_start": line_num + 1,
                "line_end": end_line + 1,
                "args": [],
                "lines": end_line - line_num + 1,
                "code": code,
                "language": language,
            })

    return units


def parse_generic_file(filepath: str, content: str, language: str) -> list[dict]:
    """Generikus fájl — az egész fájlt egyetlen egységként kezeli."""
    lines = content.splitlines()
    return [{
        "type": "file",
        "name": os.path.basename(filepath),
        "file": filepath,
        "line_start": 1,
        "line_end": len(lines),
        "args": [],
        "lines": len(lines),
        "code": content,
        "language": language,
    }]


def parse_codebase(files: dict[str, str]) -> list[dict]:
    """Összes fájl elemzése — visszaadja az összes kódegységet."""
    all_units = []
    for filepath, content in files.items():
        ext = os.path.splitext(filepath)[1].lower()
        lang = SUPPORTED_EXTENSIONS.get(ext, "Unknown")

        if ext == ".py":
            units = parse_python_file(filepath, content)
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            units = parse_js_ts_file(filepath, content, lang)
        else:
            units = parse_generic_file(filepath, content, lang)

        # Ha nem találtunk egységeket, az egész fájl egy egység
        if not units:
            units = parse_generic_file(filepath, content, lang)

        all_units.extend(units)

    return all_units


def build_structure_summary(files: dict[str, str], units: list[dict]) -> str:
    """Kódbázis struktúra összefoglaló szöveg az LLM-nek (kód nélkül, csak váz)."""
    summary = []
    summary.append(f"Fájlok száma: {len(files)}")
    summary.append(f"Kódegységek száma: {len(units)}")
    summary.append("")

    by_file = {}
    for u in units:
        by_file.setdefault(u["file"], []).append(u)

    for filepath, file_units in by_file.items():
        ext = os.path.splitext(filepath)[1].lower()
        lang = SUPPORTED_EXTENSIONS.get(ext, "?")
        summary.append(f"📄 {filepath} ({lang}, {sum(u['lines'] for u in file_units)} sor)")
        for u in file_units:
            if u["type"] == "function":
                args_str = ", ".join(u.get("args", []))
                summary.append(f"   ├─ def {u['name']}({args_str})  [{u['lines']} sor]")
            elif u["type"] == "class":
                methods = u.get("methods", [])
                summary.append(f"   ├─ class {u['name']}  [{u['lines']} sor, {len(methods)} metódus: {', '.join(methods)}]")
            elif u["type"] == "file":
                summary.append(f"   └─ (teljes fájl, {u['lines']} sor)")
        summary.append("")

    return "\n".join(summary)


def get_unit_context(unit: dict, files: dict[str, str]) -> str:
    """Kontextus generálása egy kódegységhez (importok, osztály amiben van, stb.)."""
    filepath = unit["file"]
    content = files.get(filepath, "")
    lines = content.splitlines()
    context_parts = []

    # Importok kinyerése
    imports = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            imports.append(stripped)
        elif stripped.startswith("const ") and "require(" in stripped:
            imports.append(stripped)
    if imports:
        context_parts.append(f"Importok a fájlban:\n" + "\n".join(imports[:15]))

    # Fájl útvonal
    context_parts.append(f"Fájl: {filepath}")

    # Más függvények a fájlban (csak nevek)
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".py":
        try:
            tree = ast.parse(content)
            other_funcs = [n.name for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and n.name != unit["name"]]
            if other_funcs:
                context_parts.append(f"Más függvények a fájlban: {', '.join(other_funcs)}")
        except SyntaxError:
            pass

    return "\n".join(context_parts)


# ═══════════════════════════════════════════════════════════════════
#  STREAMLIT APP
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(page_title="⚡ Kódoptimalizáló", page_icon="⚡", layout="wide")

# ── DB inicializálás ─────────────────────────────────────────────────
init_db()

# ── Custom CSS ──────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');
    .stApp { font-family: 'Plus Jakarta Sans', sans-serif; }
    .main-title {
        font-size: 2.4rem; font-weight: 700;
        background: linear-gradient(135deg, #6366f1, #06b6d4);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0;
    }
    .subtitle { color: #94a3b8; font-size: 1.05rem; margin-top: -8px; margin-bottom: 24px; }
    div[data-testid="stCodeBlock"] code { font-family: 'JetBrains Mono', monospace !important; font-size: 0.85rem !important; }
    .metric-card { background: linear-gradient(135deg, #1e1b4b, #1e3a5f); border-radius: 12px; padding: 16px 20px; border: 1px solid #334155; }
    .metric-label { color: #94a3b8; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-value { color: #e2e8f0; font-size: 1.3rem; font-weight: 700; margin-top: 4px; }
    .improvement-tag { display: inline-block; background: #064e3b; color: #6ee7b7; padding: 3px 10px; border-radius: 99px; font-size: 0.78rem; font-weight: 600; margin: 2px 4px 2px 0; }
    .tradeoff-tag { display: inline-block; background: #78350f; color: #fcd34d; padding: 3px 10px; border-radius: 99px; font-size: 0.78rem; font-weight: 600; margin: 2px 4px 2px 0; }
</style>
""", unsafe_allow_html=True)

# ── Session state ───────────────────────────────────────────────────
if "active_code_id" not in st.session_state:
    st.session_state.active_code_id = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "api_key" not in st.session_state:
    st.session_state.api_key = get_api_key_from_env() or ""

# ── Fejléc ──────────────────────────────────────────────────────────
st.markdown('<div class="main-title">⚡ Automatikus Kódoptimalizáló</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Prompt Engineering házi — LLM-alapú kódjavítás több szempont szerint</div>', unsafe_allow_html=True)

# ── Sidebar ─────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔧 Beállítások")

    # ── API kulcs kezelés ────────────────────────────────────────────
    st.subheader("🔑 API kulcs")
    key_method = st.radio(
        "Kulcs forrása",
        ["Kézi bevitel", "Mentett kulcs betöltése", "Kulcs mentése"],
        index=0,
        label_visibility="collapsed",
    )

    if key_method == "Kézi bevitel":
        typed_key = st.text_input("OpenAI API kulcs", type="password", placeholder="sk-...", value=st.session_state.api_key)
        if typed_key:
            st.session_state.api_key = typed_key
        st.caption("⚠️ A kulcs csak a session idejére tárolódik a memóriában.")

    elif key_method == "Kulcs mentése":
        st.caption("A kulcsot jelszóval titkosítva mentjük a .env fájlba.")
        save_key_input = st.text_input("API kulcs", type="password", placeholder="sk-...")
        save_pw = st.text_input("Titkosító jelszó", type="password", placeholder="Válassz egy jelszót...")
        save_pw2 = st.text_input("Jelszó mégegyszer", type="password", placeholder="Jelszó megerősítése...")
        if st.button("💾 Kulcs mentése titkosítva", use_container_width=True):
            if not save_key_input or not save_pw:
                st.error("Mindkét mező kötelező!")
            elif save_pw != save_pw2:
                st.error("A jelszavak nem egyeznek!")
            elif len(save_pw) < 4:
                st.error("A jelszó túl rövid (min. 4 karakter)!")
            else:
                save_api_key_encrypted(save_key_input, save_pw)
                st.session_state.api_key = save_key_input
                st.success("✅ Kulcs titkosítva mentve!")

    elif key_method == "Mentett kulcs betöltése":
        if has_saved_key():
            load_pw = st.text_input("Titkosító jelszó", type="password", placeholder="Add meg a jelszót...")
            lc1, lc2 = st.columns(2)
            with lc1:
                if st.button("🔓 Betöltés", use_container_width=True):
                    loaded = load_api_key_encrypted(load_pw)
                    if loaded and loaded.startswith("sk-"):
                        st.session_state.api_key = loaded
                        st.success("✅ Kulcs betöltve!")
                    else:
                        st.error("❌ Hibás jelszó!")
            with lc2:
                if st.button("🗑️ Törlés", use_container_width=True):
                    delete_saved_key()
                    st.success("Kulcs törölve.")
                    st.rerun()
        else:
            st.info("Nincs mentett kulcs. Használd a 'Kulcs mentése' opciót.")

    api_key = st.session_state.api_key

    if api_key:
        st.success(f"✅ Kulcs aktív: `{api_key[:7]}...{api_key[-4:]}`")
    else:
        st.warning("Nincs API kulcs megadva.")

    st.divider()
    st.subheader("Modell")
    model = st.selectbox("GPT modell", ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"], index=0)
    temperature = st.slider("Temperature", 0.0, 1.0, 0.3, 0.05)

    st.divider()
    st.subheader("📊 Prompt stratégia")
    strategy = st.radio("Prompt típus", ["Zero-shot", "Few-shot", "Chain-of-Thought"], index=2)

    st.divider()

    # ── Kódkönyvtár (sidebar) ────────────────────────────────────────
    st.subheader("📂 Kódkönyvtár")
    codes = db_list_codes()

    if codes:
        options = ["— Új kód —"] + [f"{c['name']}  ({c['run_count']} futtatás)" for c in codes]
        code_ids = [None] + [c["id"] for c in codes]

        selected_idx = st.selectbox("Mentett kód betöltése", range(len(options)), format_func=lambda i: options[i], key="lib_select")

        if selected_idx > 0:
            sel_id = code_ids[selected_idx]
            if st.button("📥 Betöltés", use_container_width=True):
                code_data = db_get_code(sel_id)
                st.session_state.active_code_id = sel_id
                st.session_state.ta_code_input = code_data["code"]
                st.session_state.last_result = None
                st.rerun()
            if st.button("🗑️ Törlés", use_container_width=True):
                db_delete_code(sel_id)
                if st.session_state.active_code_id == sel_id:
                    st.session_state.active_code_id = None
                st.rerun()
    else:
        st.caption("Még nincs mentett kód.")

    st.divider()
    st.caption("Készítette: Prompt Engineering kurzus házi")

# ── Optimalizálási szempontok ───────────────────────────────────────
GOALS = {
    "perf":   ("⚡ Teljesítmény",     "Futásidő és memória"),
    "read":   ("📖 Olvashatóság",     "Clean code, jobb struktúra"),
    "dry":    ("✂️ DRY / Tömörség",   "Ismétlődések megszüntetése"),
    "modern": ("🔄 Modern szintaxis", "Nyelvi újdonságok használata"),
    "safe":   ("🛡️ Biztonság",        "Edge case-ek, hibakezelés"),
}

st.subheader("🎯 Optimalizálási szempontok")
goal_cols = st.columns(len(GOALS))
selected_goals = []
for i, (gid, (label, desc)) in enumerate(GOALS.items()):
    with goal_cols[i]:
        if st.checkbox(label, value=(gid in ("perf", "read")), help=desc):
            selected_goals.append(gid)

# ── Kód bevitel + mentés ────────────────────────────────────────────
st.subheader("📝 Forráskód")

# ── Kódbázis elemzés (opcionális) ────────────────────────────────
with st.expander("📂 Kódbázis elemzés — ZIP feltöltés és függvény-kiválasztás", expanded=False):
    st.caption("Tölts fel egy ZIP fájlt, az app kielemzi a struktúrát és kiválaszthatod, melyik függvényt optimalizáld.")

    uploaded_zip = st.file_uploader("ZIP fájl feltöltése", type=["zip"], key="codebase_zip")

    if uploaded_zip:
        # Kicsomagolás és elemzés
        if "codebase_files" not in st.session_state or st.session_state.get("codebase_zip_name") != uploaded_zip.name:
            with st.spinner("📦 Kicsomagolás és elemzés..."):
                files = extract_zip(uploaded_zip)
                units = parse_codebase(files)
                st.session_state.codebase_files = files
                st.session_state.codebase_units = units
                st.session_state.codebase_zip_name = uploaded_zip.name
                st.session_state.codebase_triage = None

        files = st.session_state.codebase_files
        units = st.session_state.codebase_units

        # Struktúra összefoglaló
        st.markdown(f"**📊 Struktúra:** {len(files)} fájl, {len(units)} kódegység")
        struct_summary = build_structure_summary(files, units)
        with st.expander("🗂️ Részletes struktúra", expanded=False):
            st.code(struct_summary, language="text")

        # LLM triage
        st.markdown("---")
        st.markdown("**🔍 LLM Triage — mely részeket érdemes optimalizálni?**")

        TRIAGE_SYSTEM = """\
Te egy tapasztalt kód-reviewer vagy. Kapsz egy kódbázis struktúráját (fájlok, függvények, osztályok, sorhosszak).
Azonosítsd a TOP 5 kódegységet ami a legjobban rászorul optimalizálásra.

A válaszod KIZÁRÓLAG egyetlen VALID JSON objektum legyen — semmi más.

JSON séma:
{
  "recommendations": [
    {
      "rank": 1,
      "name": "függvény/osztály neve",
      "file": "fájl útvonal",
      "reason": "Miért érdemes optimalizálni (magyarul, 1-2 mondat)",
      "priority": "high/medium/low",
      "suspected_issues": ["issue1", "issue2"]
    }
  ],
  "overall_assessment": "Általános értékelés a kódbázisról (magyarul, 2-3 mondat)"
}"""

        if st.button("🤖 LLM Triage futtatása", use_container_width=True, key="triage_btn"):
            if not api_key:
                st.error("⚠️ Nincs API kulcs!")
            else:
                client = OpenAI(api_key=api_key)
                triage_user = f"Elemezd ezt a kódbázis struktúrát és javasold, mit érdemes optimalizálni:\n\n{struct_summary}"

                with st.spinner("🤖 A kódbázis elemzése folyamatban..."):
                    try:
                        t_resp = client.chat.completions.create(
                            model=model, temperature=0.2,
                            messages=[
                                {"role": "system", "content": TRIAGE_SYSTEM},
                                {"role": "user", "content": triage_user},
                            ],
                            max_tokens=1500,
                        )
                        t_raw = t_resp.choices[0].message.content.strip()
                        if t_raw.startswith("```"):
                            t_raw = t_raw.split("\n", 1)[1]
                            if t_raw.endswith("```"):
                                t_raw = t_raw[:-3]
                            t_raw = t_raw.strip()
                        st.session_state.codebase_triage = json.loads(t_raw)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Triage hiba: {e}")

        # Triage eredmények
        if st.session_state.get("codebase_triage"):
            triage = st.session_state.codebase_triage
            st.info(triage.get("overall_assessment", ""))
            st.markdown("**Javaslatok:**")
            for rec in triage.get("recommendations", []):
                priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rec.get("priority", ""), "⚪")
                issues = ", ".join(rec.get("suspected_issues", []))
                st.markdown(
                    f"{priority_icon} **#{rec.get('rank', '?')} {rec.get('name', '?')}** "
                    f"(`{rec.get('file', '?')}`) — {rec.get('reason', '')}\n"
                    f"  Gyanított problémák: *{issues}*"
                )

        # Kódegység kiválasztó
        st.markdown("---")
        st.markdown("**⬇️ Kódegység betöltése az optimalizálóba:**")

        unit_labels = [f"{u['type']} {u['name']} ({u['file']}, {u['lines']} sor)" for u in units]
        if units:
            selected_unit_idx = st.selectbox("Válassz kódegységet:", range(len(units)), format_func=lambda i: unit_labels[i], key="unit_select")
            selected_unit = units[selected_unit_idx]

            # Név automatikus frissítése ha változik a kiválasztás
            auto_name = f"{selected_unit['name']} ({os.path.basename(selected_unit['file'])})"
            if selected_unit["type"] == "file":
                auto_name = f"{selected_unit['file']}"
            if st.session_state.get("_last_unit_idx") != selected_unit_idx:
                st.session_state._last_unit_idx = selected_unit_idx
                st.session_state.cb_unit_name = auto_name

            # Kontextus megjelenítés
            context = get_unit_context(selected_unit, files)
            with st.expander("📎 Kontextus (ez is bekerül a promptba)", expanded=False):
                st.code(context, language="text")

            load_cols = st.columns([2, 2, 1])
            with load_cols[0]:
                cb_name = st.text_input(
                    "Név:",
                    key="cb_unit_name",
                    label_visibility="collapsed",
                    placeholder="Kód neve...",
                )
            with load_cols[1]:
                include_context = st.checkbox("Kontextus hozzáadása a prompthoz", value=True, key="cb_include_ctx")
            with load_cols[2]:
                if st.button("⬇️ Betöltés", use_container_width=True, key="cb_load_btn"):
                    # Kontextus hozzáfűzése a kódhoz kommentben
                    code_to_load = selected_unit["code"]
                    if include_context and context.strip():
                        ctx_comment = f"# === KONTEXTUS (a fájlból) ===\n# {context.replace(chr(10), chr(10) + '# ')}\n# === OPTIMALIZÁLANDÓ KÓD ===\n\n"
                        code_to_load = ctx_comment + code_to_load

                    # Mentés a kódkönyvtárba + betöltés
                    new_id = db_save_code(cb_name.strip() or selected_unit["name"], code_to_load)
                    st.session_state.active_code_id = new_id
                    st.session_state.ta_code_input = code_to_load
                    st.session_state.last_result = None
                    st.success(f"✅ **{cb_name}** betöltve és elmentve!")
                    st.rerun()
        else:
            st.warning("Nem találtam elemzehető kódegységet a ZIP-ben.")

active_id = st.session_state.active_code_id
if active_id:
    active_code = db_get_code(active_id)
    if active_code:
        run_count = len(db_get_runs(active_id))
        st.info(f"📂 Aktív kód: **{active_code['name']}** — {run_count} korábbi futtatás")
    else:
        st.session_state.active_code_id = None
        active_id = None

EXAMPLE = '''\
function findDuplicates(arr) {
  let duplicates = [];
  for (let i = 0; i < arr.length; i++) {
    for (let j = i + 1; j < arr.length; j++) {
      if (arr[i] === arr[j]) {
        if (duplicates.indexOf(arr[i]) === -1) {
          duplicates.push(arr[i]);
        }
      }
    }
  }
  return duplicates;
}'''

if "ta_code_input" not in st.session_state:
    st.session_state.ta_code_input = EXAMPLE

code_input = st.text_area("Kód:", height=260, key="ta_code_input", label_visibility="collapsed")

save_col1, save_col2 = st.columns([3, 1])
with save_col1:
    default_name = active_code["name"] if active_id and active_id and db_get_code(active_id) else ""
    save_name = st.text_input("Kód neve:", value=default_name, placeholder="pl. SQL injection teszt", label_visibility="collapsed")
with save_col2:
    if st.button("💾 Mentés", use_container_width=True):
        if not save_name.strip():
            st.error("Adj nevet a kódnak!")
        elif not code_input.strip():
            st.error("Üres a kód!")
        else:
            if active_id and db_get_code(active_id):
                db_update_code(active_id, save_name.strip(), code_input)
                st.success(f"✅ **{save_name}** frissítve!")
            else:
                new_id = db_save_code(save_name.strip(), code_input)
                st.session_state.active_code_id = new_id
                st.success(f"✅ **{save_name}** elmentve!")
            st.rerun()


# ── Prompt építés ───────────────────────────────────────────────────
def build_system_prompt(goals, strategy):
    goal_lines = "\n".join(f"- {GOALS[g][0]}: {GOALS[g][1]}" for g in goals)
    return f"""\
You are a senior software engineer specializing in code optimization.
The user provides a code snippet, and you MUST generate EXACTLY 3 optimized alternatives. No more, no less.

Optimization goals:
{goal_lines}

The response MUST be strictly valid JSON parsable by standard JSON parsers.
Do not include trailing commas or comments.
No markdown code fences, no introductory or closing text.

JSON schema:
{{
  "detected_language": "Programming language of the code (e.g., Python, JavaScript, Java)",
  "analysis": "Brief analysis of the main issues in the original code (2-3 sentences)",
  "alternatives": [
    {{
      "title": "Title",
      "code": "Optimized code (fully working, not pseudocode)",
      "explanation": "What changed and why (2-3 sentences)",
      "improvements": ["improvement1", "improvement2"],
      "tradeoffs": ["tradeoff1"]
    }}
  ]
}}

Each alternative MUST preserve the original function name and signature."""


DEFAULT_COT = """\
Think step by step:
1. First, identify the time and space complexity of the original code.
2. List the specific weaknesses.
3. For each alternative, explain your reasoning for choosing that solution.
4. Finally, format the JSON."""


def build_few_shot_messages():
    inp = """\
def sum_list(lst):
    total = 0
    i = 0
    while i < len(lst):
        total = total + lst[i]
        i = i + 1
    return total"""
    out = json.dumps({
        "detected_language": "Python",
        "analysis": "The code uses a manual loop with indexing to sum elements — Python has built-in tools for this.",
        "alternatives": [
            {"title": "Built-in sum()", "code": "def sum_list(lst):\n    return sum(lst)", "explanation": "sum() is implemented in C, making it faster and more readable.", "improvements": ["Reduced to a single line", "Native C speed"], "tradeoffs": ["Less didactic for beginners"]},
            {"title": "For-each loop", "code": "def sum_list(lst):\n    total = 0\n    for item in lst:\n        total += item\n    return total", "explanation": "Pythonic iteration instead of manual indexing; += operator is more concise.", "improvements": ["More readable", "No index-out-of-range risk"], "tradeoffs": ["Slightly slower than sum()"]},
            {"title": "functools.reduce", "code": "from functools import reduce\ndef sum_list(lst):\n    return reduce(lambda a, b: a + b, lst, 0)", "explanation": "Functional approach — reduce folds elements together.", "improvements": ["Functional style", "Generalizable to other operations"], "tradeoffs": ["Harder to read", "Requires import"]},
        ]
    }, ensure_ascii=False, indent=2)
    return [{"role": "user", "content": f"Optimize this code:\n\n```\n{inp}\n```"}, {"role": "assistant", "content": out}]
    inp = """\
def sum_list(lst):
    total = 0
    i = 0
    while i < len(lst):
        total = total + lst[i]
        i = i + 1
    return total"""
    out = json.dumps({
        {
  "detected_language": "Python",
  "analysis": "The code uses a manual loop with indexing to sum elements — Python has built-in tools for this.",
  "alternatives": [
    {
      "title": "Built-in sum()",
      "code": "def sum_list(lst):\n    return sum(lst)",
      "explanation": "sum() is implemented in C, making it faster.",
      "improvements": [
        "Reduced to a single line"
      ],
      "tradeoffs": [
        "Less didactic for beginners"
      ]
    },
    {
      "title": "For-each loop",
      "code": "def sum_list(lst):\n    total = 0\n    for item in lst:\n        total += item\n    return total",
      "explanation": "Pythonic iteration.",
      "improvements": [
        "More readable"
      ],
      "tradeoffs": [
        "Slower than sum()"
      ]
    },
    {
      "title": "functools.reduce",
      "code": "from functools import reduce\ndef sum_list(lst):\n    return reduce(lambda a, b: a + b, lst, 0)",
      "explanation": "Functional approach.",
      "improvements": [
        "Functional style"
      ],
      "tradeoffs": [
        "Harder to read"
      ]
    }
  ]
}
    }, ensure_ascii=False, indent=2)
    return [{"role": "user", "content": f"Optimize this code:\n\n```\n{inp}\n```"}, {"role": "assistant", "content": out}]


def build_user_message(code):
    return f"Optimize this code:\n\n```\n{code}\n```"


# ── Prompt előnézet & szerkesztés ──────────────────────────────────
st.divider()
st.subheader("✏️ Prompt szerkesztő & előnézet")
st.caption("A promptok automatikusan generálódnak. Küldés előtt szabadon szerkesztheted őket.")

default_system = build_system_prompt(selected_goals if selected_goals else ["perf"], strategy)
default_user_msg = build_user_message(code_input)

regen_key = f"{strategy}|{'|'.join(sorted(selected_goals))}|{hash(code_input)}"
if "last_regen_key" not in st.session_state or st.session_state.last_regen_key != regen_key:
    st.session_state.last_regen_key = regen_key
    st.session_state.ta_system = default_system
    st.session_state.ta_user = default_user_msg
    if strategy == "Few-shot":
        fs = build_few_shot_messages()
        st.session_state.ta_fs_user = fs[0]["content"]
        st.session_state.ta_fs_asst = fs[1]["content"]
    if strategy == "Chain-of-Thought":
        st.session_state.ta_cot = DEFAULT_COT

prompt_tabs = st.tabs(
    ["🔧 System prompt", "💬 User prompt"]
    + (["📚 Few-shot (user)", "📚 Few-shot (assistant)"] if strategy == "Few-shot" else [])
    + (["🧠 CoT kiegészítés"] if strategy == "Chain-of-Thought" else [])
)
with prompt_tabs[0]:
    st.text_area("System prompt", height=320, key="ta_system", label_visibility="collapsed")
with prompt_tabs[1]:
    st.text_area("User prompt", height=180, key="ta_user", label_visibility="collapsed")
if strategy == "Few-shot":
    with prompt_tabs[2]:
        st.text_area("Few-shot user", height=180, key="ta_fs_user", label_visibility="collapsed")
    with prompt_tabs[3]:
        st.text_area("Few-shot assistant", height=300, key="ta_fs_asst", label_visibility="collapsed")
if strategy == "Chain-of-Thought":
    with prompt_tabs[2]:
        st.markdown("**Chain-of-Thought kiegészítés** — futtatáskor a system prompt végéhez fűződik.")
        st.text_area("CoT kiegészítés", height=180, key="ta_cot", label_visibility="collapsed")

if st.button("🔄 Promptok visszaállítása"):
    st.session_state.ta_system = default_system
    st.session_state.ta_user = default_user_msg
    if strategy == "Few-shot":
        fs = build_few_shot_messages()
        st.session_state.ta_fs_user = fs[0]["content"]
        st.session_state.ta_fs_asst = fs[1]["content"]
    if strategy == "Chain-of-Thought":
        st.session_state.ta_cot = DEFAULT_COT
    st.rerun()

# ── Futtatás ────────────────────────────────────────────────────────
st.divider()
run = st.button("🚀 Optimalizálás indítása", type="primary", use_container_width=True)

if run:
    if not api_key:
        st.error("⚠️ Add meg az OpenAI API kulcsot!")
        st.stop()
    if not selected_goals:
        st.error("⚠️ Válassz legalább egy szempontot!")
        st.stop()
    if not code_input.strip():
        st.error("⚠️ Üres a kód!")
        st.stop()
    if not active_id:
        st.error("⚠️ Előbb mentsd el a kódot névvel, hogy a futtatás hozzá legyen rendelve!")
        st.stop()

    final_system = st.session_state.ta_system
    cot_text = None
    if strategy == "Chain-of-Thought" and "ta_cot" in st.session_state:
        cot_text = st.session_state.ta_cot
        final_system = final_system.rstrip() + "\n\n" + cot_text
    final_user_msg = st.session_state.ta_user

    all_messages = [{"role": "system", "content": final_system}]
    if strategy == "Few-shot":
        all_messages.append({"role": "user", "content": st.session_state.ta_fs_user})
        all_messages.append({"role": "assistant", "content": st.session_state.ta_fs_asst})
    all_messages.append({"role": "user", "content": final_user_msg})

    client = OpenAI(api_key=api_key)

    with st.expander("📨 Elküldött üzenetek", expanded=False):
        st.markdown(f"**Stratégia:** `{strategy}` · **Modell:** `{model}` · **Temperature:** `{temperature}`")
        for i, msg in enumerate(all_messages):
            role_label = {"system": "🔧 SYSTEM", "user": "💬 USER", "assistant": "🤖 ASSISTANT"}[msg["role"]]
            st.markdown(f"**{role_label}** (#{i+1})")
            st.code(msg["content"], language="text")

    with st.spinner("🤖 Az LLM optimalizálja a kódot..."):
        try:
            response = client.chat.completions.create(model=model, temperature=temperature, messages=all_messages, max_tokens=3500)
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            result = json.loads(raw)
        except json.JSONDecodeError:
            st.error("❌ Nem valid JSON. Próbáld újra.")
            with st.expander("Nyers válasz"):
                st.code(raw, language="json")
            st.stop()
        except Exception as e:
            st.error(f"❌ API hiba: {e}")
            st.stop()

    run_data = {
        "strategy": strategy, "model": model, "temperature": temperature,
        "goals": selected_goals.copy(),
        "system_prompt": st.session_state.ta_system,  # CoT nélkül mentjük
        "user_prompt": final_user_msg,
        "cot_prompt": cot_text,
        "original_code": code_input,
        "detected_language": result.get("detected_language", "unknown"),
        "result": result,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }

    # Mentés adatbázisba
    db_save_run(active_id, run_data)
    st.session_state.last_result = run_data
    st.rerun()

# ═══════════════════════════════════════════════════════════════════
#  EREDMÉNYEK MEGJELENÍTÉSE
# ═══════════════════════════════════════════════════════════════════

if st.session_state.last_result:
    rd = st.session_state.last_result
    result = rd["result"]
    usage = rd["usage"]
    original_code = rd["original_code"]
    detected_lang = rd.get("detected_language", "text")

    st.divider()
    mcols = st.columns(5)
    for col, (label, val) in zip(mcols, [
        ("Felismert nyelv", detected_lang), ("Prompt tokenek", usage["prompt_tokens"]),
        ("Válasz tokenek", usage["completion_tokens"]), ("Összes token", usage["total_tokens"]),
        ("Stratégia", rd["strategy"]),
    ]):
        with col:
            st.markdown(f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{val}</div></div>', unsafe_allow_html=True)

    st.divider()
    st.subheader("🔎 Elemzés")
    st.info(result.get("analysis", "—"))

    st.subheader("💡 Generált alternatívák")
    alternatives = result.get("alternatives", [])
    alt_tabs = st.tabs([f"#{i+1} — {a.get('title', '?')}" for i, a in enumerate(alternatives)])
    for i, (tab, alt) in enumerate(zip(alt_tabs, alternatives)):
        with tab:
            st.markdown(f"**{alt.get('title', '')}**")
            st.markdown(alt.get("explanation", ""))
            st.code(alt.get("code", ""), language=detected_lang.lower())
            tc1, tc2 = st.columns(2)
            with tc1:
                st.markdown("**✅ Javítások:**")
                st.markdown("".join(f'<span class="improvement-tag">{x}</span>' for x in alt.get("improvements", [])), unsafe_allow_html=True)
            with tc2:
                st.markdown("**⚠️ Kompromisszumok:**")
                tags = "".join(f'<span class="tradeoff-tag">{x}</span>' for x in alt.get("tradeoffs", []))
                st.markdown(tags or '<span class="tradeoff-tag">Nincs</span>', unsafe_allow_html=True)

    st.divider()
    st.subheader("📊 Összehasonlítás")
    st.table({
        "Alternatíva": [f"#{i+1} {a.get('title','')}" for i, a in enumerate(alternatives)],
        "Javítások": [len(a.get("improvements", [])) for a in alternatives],
        "Kompromisszumok": [len(a.get("tradeoffs", [])) for a in alternatives],
        "Sorok": [len(a.get("code", "").strip().splitlines()) for a in alternatives],
    })

    st.divider()
    st.subheader("🆚 Eredeti vs. Legjobb alternatíva")
    oc, nc = st.columns(2)
    with oc:
        st.markdown("**Eredeti kód**")
        st.code(original_code, language=detected_lang.lower())
        st.metric("Sorok száma", len(original_code.strip().splitlines()))
    with nc:
        best = min(alternatives, key=lambda a: len(a.get("tradeoffs", [])))
        st.markdown(f"**{best.get('title', 'Legjobb')}**")
        st.code(best.get("code", ""), language=detected_lang.lower())
        st.metric("Sorok száma", len(best.get("code", "").strip().splitlines()))

# ═══════════════════════════════════════════════════════════════════
#  FUTTATÁSI ELŐZMÉNYEK (adatbázisból)
# ═══════════════════════════════════════════════════════════════════

active_id = st.session_state.active_code_id
if active_id and db_get_code(active_id):
    runs = db_get_runs(active_id)
    code_data = db_get_code(active_id)

    if runs:
        st.divider()
        st.subheader(f"📜 Futtatási előzmények — {code_data['name']}")
        st.caption(f"{len(runs)} futtatás az adatbázisban")

        st.dataframe({
            "#": list(range(1, len(runs)+1)),
            "Időpont": [r["timestamp"] for r in runs],
            "Stratégia": [r["strategy"] for r in runs],
            "Modell": [r["model"] for r in runs],
            "Temp.": [r["temperature"] for r in runs],
            "Szempontok": [", ".join(r["goals"]) for r in runs],
            "Tokenek": [r["usage"]["total_tokens"] for r in runs],
            "Nyelv": [r.get("detected_language", "?") for r in runs],
        }, use_container_width=True, hide_index=True)

        run_labels = [f"#{i+1} — {r['strategy']} ({r['timestamp']})" for i, r in enumerate(runs)]
        sel = st.selectbox("Korábbi futtatás:", range(len(runs)), format_func=lambda i: run_labels[i])
        old = runs[sel]

        with st.expander("📨 Elküldött prompt", expanded=False):
            st.markdown(f"`{old['strategy']}` · `{old['model']}` · temp={old['temperature']}")
            st.markdown("**System prompt:**")
            st.code(old["system_prompt"], language="text")
            if old.get("cot_prompt"):
                st.markdown("**CoT kiegészítés:**")
                st.code(old["cot_prompt"], language="text")
            st.markdown("**User prompt:**")
            st.code(old["user_prompt"], language="text")

        with st.expander("💡 Alternatívák", expanded=True):
            st.markdown(f"**Elemzés:** {old['result'].get('analysis', '—')}")
            for j, alt in enumerate(old["result"].get("alternatives", [])):
                st.markdown(f"---\n**#{j+1} — {alt.get('title', '')}**")
                st.markdown(alt.get("explanation", ""))
                st.code(alt.get("code", ""), language=old.get("detected_language", "text").lower())

        # ═══════════════════════════════════════════════════════════════
        #  LLM-AS-JUDGE
        # ═══════════════════════════════════════════════════════════════

        st.divider()
        st.subheader("🏆 LLM-as-Judge — Automatikus értékelés")
        st.caption("Válassz 1-3 futtatást, és egy független LLM pontozza az alternatívákat (1-10).")

        JUDGE_SYSTEM_SINGLE = """\
Te egy független kódbíráló vagy. Kapsz egy eredeti kódot és annak optimalizált alternatíváit.
Minden alternatívát értékelj az alábbi szempontok szerint 1-10 skálán.

A válaszod KIZÁRÓLAG egyetlen VALID JSON objektum legyen — semmi más.
Nincs markdown, nincs backtick, nincs bevezető szöveg.

JSON séma:
{
  "evaluations": [
    {
      "alternative_index": 1,
      "title": "Az alternatíva címe",
      "scores": {
        "performance": 8,
        "readability": 7,
        "security": 9,
        "maintainability": 6,
        "overall": 7
      },
      "reasoning": "Rövid indoklás magyarul (2-3 mondat)",
      "strengths": ["erősség1"],
      "weaknesses": ["gyengeség1"]
    }
  ],
  "winner": 1,
  "winner_reasoning": "Miért ez a legjobb összességében (1-2 mondat, magyarul)"
}

Légy szigorú és objektív. A pontszámoknak tükrözniük kell a tényleges minőségkülönbségeket."""

        JUDGE_SYSTEM_MULTI = """\
Te egy független kódbíráló vagy. Kapsz egy eredeti kódot és TÖBB FUTTATÁS eredményeit,
amelyek különböző prompting stratégiákkal készültek (pl. Zero-shot, Few-shot, Chain-of-Thought).
Minden futtatás legjobb alternatíváját értékeld szempontonként 1-10 skálán,
és hasonlítsd össze a stratégiák hatékonyságát.

A válaszod KIZÁRÓLAG egyetlen VALID JSON objektum legyen — semmi más.

JSON séma:
{
  "run_evaluations": [
    {
      "run_label": "A futtatás azonosítója (pl. '#1 Zero-shot')",
      "strategy": "A prompting stratégia neve",
      "best_alternative_title": "A legjobb alternatíva címe",
      "scores": {
        "performance": 8,
        "readability": 7,
        "security": 9,
        "maintainability": 6,
        "overall": 7
      },
      "reasoning": "Rövid indoklás magyarul (2-3 mondat)",
      "strengths": ["erősség1"],
      "weaknesses": ["gyengeség1"]
    }
  ],
  "overall_winner_run": "#1 Zero-shot",
  "comparison_summary": "Összefoglaló: melyik stratégia miben volt jobb/rosszabb (3-4 mondat, magyarul)",
  "strategy_ranking": ["Chain-of-Thought", "Few-shot", "Zero-shot"]
}

Légy szigorú és objektív. Koncentrálj a STRATÉGIÁK KÖZTI KÜLÖNBSÉGEKRE."""

        def build_judge_prompt_single(original, alts, lang):
            alt_texts = []
            for i, a in enumerate(alts):
                alt_texts.append(f"### Alternatíva #{i+1}: {a.get('title','')}\n```{lang}\n{a.get('code','')}\n```\nMagyarázat: {a.get('explanation','')}")
            return f"""Eredeti kód ({lang}):
```{lang}
{original}
```

Alternatívák:
{chr(10).join(alt_texts)}

Értékeld mindegyik alternatívát!"""

        def build_judge_prompt_multi(original, selected_runs_judge, lang):
            parts = []
            for r in selected_runs_judge:
                label = f"#{runs.index(r)+1} {r['strategy']}"
                best = min(r["result"].get("alternatives", [{}]), key=lambda a: len(a.get("tradeoffs", [])))
                parts.append(
                    f"### Futtatás: {label} (modell: {r['model']}, temp: {r['temperature']})\n"
                    f"**Legjobb alternatíva: {best.get('title', '?')}**\n"
                    f"```{lang}\n{best.get('code', '')}\n```\n"
                    f"Magyarázat: {best.get('explanation', '')}\n"
                    f"Javítások: {', '.join(best.get('improvements', []))}\n"
                    f"Kompromisszumok: {', '.join(best.get('tradeoffs', []))}"
                )
            return f"""Eredeti kód ({lang}):
```{lang}
{original}
```

Az alábbi futtatások különböző prompting stratégiákkal készültek.
Mindegyikből a legjobb alternatívát mutatom:

{chr(10).join(parts)}

Értékeld és hasonlítsd össze a stratégiákat!"""

        # ── Hány futtatást értékelünk ────────────────────────────────
        max_judge = min(3, len(runs))
        judge_count = st.radio(
            "Hány futtatást értékeljen a bíró?",
            list(range(1, max_judge + 1)),
            index=0,
            horizontal=True,
            key="judge_count_radio",
            help="1 = egy futtatás alternatíváit pontozza | 2-3 = stratégiákat hasonlít össze",
        )

        # ── Futtatás kiválasztók ─────────────────────────────────────
        judge_labels = {1: ["Futtatás:"], 2: ["① Első:", "② Második:"], 3: ["① Első:", "② Második:", "③ Harmadik:"]}
        judge_defaults = {1: [len(runs)-1], 2: [0, len(runs)-1], 3: [0, min(1, len(runs)-1), len(runs)-1]}

        jsel_cols = st.columns(judge_count)
        judge_selected_indices = []
        for ci in range(judge_count):
            with jsel_cols[ci]:
                def_idx = judge_defaults[judge_count][ci] if ci < len(judge_defaults[judge_count]) else 0
                jidx = st.selectbox(
                    judge_labels[judge_count][ci],
                    range(len(runs)),
                    index=def_idx,
                    format_func=lambda i, _l=run_labels: _l[i],
                    key=f"judge_sel_{judge_count}_{ci}",
                )
                judge_selected_indices.append(jidx)

        judge_selected_runs = [runs[i] for i in judge_selected_indices]
        judge_lang = judge_selected_runs[0].get("detected_language", "text")
        judge_original = judge_selected_runs[0]["original_code"]

        if "judge_result" not in st.session_state:
            st.session_state.judge_result = None
            st.session_state.judge_mode = None

        judge_col1, judge_col2 = st.columns([3, 1])
        with judge_col1:
            judge_model = st.selectbox("Bíráló modell:", ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"], index=0, key="judge_model")
        with judge_col2:
            st.markdown("<br>", unsafe_allow_html=True)
            judge_go = st.button("🏆 Értékelés indítása", use_container_width=True)

        if judge_go:
            if not api_key:
                st.error("⚠️ Nincs API kulcs!")
            else:
                client = OpenAI(api_key=api_key)

                if judge_count == 1:
                    sys_prompt = JUDGE_SYSTEM_SINGLE
                    usr_prompt = build_judge_prompt_single(
                        judge_original,
                        judge_selected_runs[0]["result"].get("alternatives", []),
                        judge_lang,
                    )
                else:
                    sys_prompt = JUDGE_SYSTEM_MULTI
                    usr_prompt = build_judge_prompt_multi(judge_original, judge_selected_runs, judge_lang)

                with st.expander("📨 Judge prompt", expanded=False):
                    st.code(sys_prompt, language="text")
                    st.code(usr_prompt, language="text")

                with st.spinner("🏆 Az LLM értékeli az alternatívákat..."):
                    try:
                        j_response = client.chat.completions.create(
                            model=judge_model,
                            temperature=0.1,
                            messages=[
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": usr_prompt},
                            ],
                            max_tokens=3000,
                        )
                        j_raw = j_response.choices[0].message.content.strip()
                        if j_raw.startswith("```"):
                            j_raw = j_raw.split("\n", 1)[1]
                            if j_raw.endswith("```"):
                                j_raw = j_raw[:-3]
                            j_raw = j_raw.strip()
                        st.session_state.judge_result = json.loads(j_raw)
                        st.session_state.judge_tokens = j_response.usage.total_tokens
                        st.session_state.judge_mode = "single" if judge_count == 1 else "multi"
                        st.session_state.judge_selected_labels = [run_labels[i] for i in judge_selected_indices]
                        st.rerun()
                    except json.JSONDecodeError:
                        st.error("❌ Nem valid JSON. Próbáld újra.")
                        with st.expander("Nyers válasz"):
                            st.code(j_raw, language="json")
                    except Exception as e:
                        st.error(f"❌ Judge hiba: {e}")

        # ── Eredmények megjelenítése ─────────────────────────────────
        if st.session_state.get("judge_result"):
            jr = st.session_state.judge_result
            mode = st.session_state.get("judge_mode", "single")
            jlabels = st.session_state.get("judge_selected_labels", [])

            if mode == "single":
                # ── Egyetlen futtatás értékelése ─────────────────────────
                evals = jr.get("evaluations", [])
                winner_idx = jr.get("winner", 1)

                st.info(f"📋 Értékelt futtatás: **{jlabels[0] if jlabels else '?'}**")

                st.markdown("**📊 Pontszámok (1-10):**")
                score_data = {"Szempont": ["Teljesítmény", "Olvashatóság", "Biztonság", "Karbantarthatóság", "Összesített"]}
                score_keys = ["performance", "readability", "security", "maintainability", "overall"]

                for ev in evals:
                    prefix = "🏆 " if ev.get("alternative_index") == winner_idx else ""
                    col_label = f"{prefix}#{ev.get('alternative_index', '?')} {ev.get('title', '')}"
                    scores = ev.get("scores", {})
                    score_data[col_label] = [str(scores.get(k, "—")) for k in score_keys]

                st.table(score_data)
                st.success(f"🏆 **Győztes: Alternatíva #{winner_idx}** — {jr.get('winner_reasoning', '')}")

                for ev in evals:
                    eidx = ev.get("alternative_index", "?")
                    is_winner = eidx == winner_idx
                    icon = "🏆" if is_winner else "📋"
                    with st.expander(f"{icon} #{eidx} — {ev.get('title', '')} — részletes értékelés", expanded=is_winner):
                        st.markdown(f"**Indoklás:** {ev.get('reasoning', '—')}")
                        str_tags = " ".join(f'<span class="improvement-tag">{x}</span>' for x in ev.get("strengths", []))
                        weak_tags = " ".join(f'<span class="tradeoff-tag">{x}</span>' for x in ev.get("weaknesses", []))
                        st.markdown(f"**Erősségek:** {str_tags}", unsafe_allow_html=True)
                        st.markdown(f"**Gyengeségek:** {weak_tags}", unsafe_allow_html=True)

            else:
                # ── Több futtatás összehasonlítása ───────────────────────
                run_evals = jr.get("run_evaluations", [])
                overall_winner = jr.get("overall_winner_run", "?")
                summary = jr.get("comparison_summary", "")
                ranking = jr.get("strategy_ranking", [])

                st.info(f"📋 Értékelt futtatások: **{' vs '.join(jlabels)}**")

                # Pontszám táblázat
                st.markdown("**📊 Stratégiák pontszámai (1-10):**")
                score_data = {"Szempont": ["Teljesítmény", "Olvashatóság", "Biztonság", "Karbantarthatóság", "Összesített"]}
                score_keys = ["performance", "readability", "security", "maintainability", "overall"]

                for rev in run_evals:
                    is_winner = rev.get("run_label", "") == overall_winner
                    prefix = "🏆 " if is_winner else ""
                    col_label = f"{prefix}{rev.get('run_label', '?')}"
                    scores = rev.get("scores", {})
                    score_data[col_label] = [str(scores.get(k, "—")) for k in score_keys]

                st.table(score_data)

                # Győztes és ranking
                st.success(f"🏆 **Legjobb stratégia: {overall_winner}**")
                st.markdown(f"**Összefoglaló:** {summary}")

                if ranking:
                    st.markdown("**Stratégia rangsor:**")
                    for ri, strat in enumerate(ranking):
                        medal = ["🥇", "🥈", "🥉"][ri] if ri < 3 else f"#{ri+1}"
                        st.markdown(f"{medal} {strat}")

                # Részletes értékelések
                for rev in run_evals:
                    is_winner = rev.get("run_label", "") == overall_winner
                    icon = "🏆" if is_winner else "📋"
                    with st.expander(f"{icon} {rev.get('run_label', '?')} — {rev.get('best_alternative_title', '')}", expanded=is_winner):
                        st.markdown(f"**Indoklás:** {rev.get('reasoning', '—')}")
                        str_tags = " ".join(f'<span class="improvement-tag">{x}</span>' for x in rev.get("strengths", []))
                        weak_tags = " ".join(f'<span class="tradeoff-tag">{x}</span>' for x in rev.get("weaknesses", []))
                        st.markdown(f"**Erősségek:** {str_tags}", unsafe_allow_html=True)
                        st.markdown(f"**Gyengeségek:** {weak_tags}", unsafe_allow_html=True)

            st.caption(f"Judge token-felhasználás: {st.session_state.get('judge_tokens', '?')}")

        if len(runs) >= 2:
            st.markdown("---")
            st.subheader("🔀 Futtatások összehasonlítása")

            # ── Futtatás kiválasztók ─────────────────────────────────────
            num_compare = min(3, len(runs))
            labels_map = {0: "① Bal", 1: "② Közép", 2: "③ Jobb"}
            if num_compare == 2:
                labels_map = {0: "① Bal", 1: "② Jobb"}

            # Alapértelmezett indexek: első, (második ha van), utolsó
            if num_compare == 2:
                defaults = [0, len(runs) - 1]
            else:
                defaults = [0, 1, len(runs) - 1]

            sel_cols = st.columns(num_compare)
            selected_indices = []
            for ci in range(num_compare):
                with sel_cols[ci]:
                    idx = st.selectbox(
                        labels_map[ci],
                        range(len(runs)),
                        index=defaults[ci],
                        format_func=lambda i, _labels=run_labels: _labels[i],
                        key=f"cmpsel_{num_compare}_{ci}",
                    )
                    selected_indices.append(idx)

            selected_runs = [runs[i] for i in selected_indices]

            # ── Segédfüggvények az elemzéshez ────────────────────────────
            def get_all_improvements(r):
                """Összes improvement egy futtatás összes alternatívájából."""
                imps = set()
                for alt in r["result"].get("alternatives", []):
                    for x in alt.get("improvements", []):
                        imps.add(x.lower().strip())
                return imps

            def get_best_alt(r):
                """Legjobb alternatíva (legkevesebb kompromisszum)."""
                alts = r["result"].get("alternatives", [{}])
                return min(alts, key=lambda a: len(a.get("tradeoffs", [])))

            def avg_code_lines(r):
                """Alternatívák átlagos sorhossza."""
                alts = r["result"].get("alternatives", [])
                if not alts:
                    return 0
                return sum(len(a.get("code", "").strip().splitlines()) for a in alts) / len(alts)

            def count_goal_coverage(r):
                """Hány kiválasztott szempontot érint ténylegesen az elemzés."""
                GOAL_KEYWORDS = {
                    "perf": ["teljesítmény", "gyorsabb", "o(n)", "o(1)", "komplexitás", "memória", "sebesség", "hatékony", "lassú", "performance"],
                    "read": ["olvasható", "clean", "struktúra", "átlátható", "egyszerű", "karbantartható", "readab"],
                    "dry": ["dry", "ismétlődés", "dupliká", "tömör", "redundan"],
                    "modern": ["modern", "arrow", "async", "await", "const", "let", "destruktúr", "spread", "template", "f-string", "comprehension", "walrus"],
                    "safe": ["biztonság", "injection", "hibakezelés", "error", "exception", "try", "edge case", "validál", "xss", "sanitiz"],
                }
                text = json.dumps(r["result"], ensure_ascii=False).lower()
                covered = []
                for goal in r["goals"]:
                    keywords = GOAL_KEYWORDS.get(goal, [])
                    if any(kw in text for kw in keywords):
                        covered.append(goal)
                return covered

            original_lines = len(selected_runs[0]["original_code"].strip().splitlines())

            # ── 1. Metrika táblázat ──────────────────────────────────────
            st.markdown("**📊 Metrikák összehasonlítása:**")

            all_imps = [get_all_improvements(r) for r in selected_runs]
            all_coverages = [count_goal_coverage(r) for r in selected_runs]

            metric_data = {
                "Metrika": [
                    "Stratégia",
                    "Modell",
                    "Temperature",
                    "Összes token",
                    "Talált javítások száma",
                    "Token / javítás (hatékonyság)",
                    f"Legjobb alt. sorhossza (eredeti: {original_lines})",
                    "Sorhossz-csökkenés (%)",
                    f"Szempontok lefedettsége (/ {len(selected_runs[0]['goals'])})",
                ],
            }

            for i, r in enumerate(selected_runs):
                best = get_best_alt(r)
                best_lines = len(best.get("code", "").strip().splitlines())
                imp_count = len(all_imps[i])
                tokens = r["usage"]["total_tokens"]
                efficiency = round(tokens / imp_count, 1) if imp_count > 0 else "∞"
                line_reduction = round((1 - best_lines / original_lines) * 100, 1) if original_lines > 0 else 0
                coverage = all_coverages[i]

                label = f"#{runs.index(r)+1} {r['strategy']}"
                metric_data[label] = [
                    r["strategy"],
                    r["model"],
                    r["temperature"],
                    tokens,
                    imp_count,
                    efficiency,
                    best_lines,
                    f"{line_reduction}%",
                    f"{len(coverage)} / {len(r['goals'])}  ({', '.join(coverage) if coverage else '—'})",
                ]

            st.table(metric_data)

            # ── 2. Közös vs. egyedi javítások ────────────────────────────
            st.markdown("**🔍 Közös vs. egyedi javítások:**")

            common = all_imps[0]
            for s in all_imps[1:]:
                common = common & s

            if common:
                st.markdown("**Mindegyik futtatás megtalálta:**")
                st.markdown(" ".join(f'<span class="improvement-tag">{x}</span>' for x in sorted(common)), unsafe_allow_html=True)
            else:
                st.markdown("*Nincs közös javítás a kiválasztott futtatások között.*")

            unique_cols = st.columns(num_compare)
            for ci, (r, imps) in enumerate(zip(selected_runs, all_imps)):
                with unique_cols[ci]:
                    others = set()
                    for j, s in enumerate(all_imps):
                        if j != ci:
                            others |= s
                    unique = imps - others
                    st.markdown(f"**Csak #{runs.index(r)+1} ({r['strategy']}):**")
                    if unique:
                        st.markdown(" ".join(f'<span class="improvement-tag">{x}</span>' for x in sorted(unique)), unsafe_allow_html=True)
                    else:
                        st.caption("Nincs egyedi javítás")

            # ── 3. Legjobb alternatívák egymás mellett ───────────────────
            st.markdown("---")
            st.markdown("**💡 Legjobb alternatívák:**")

            best_cols = st.columns(num_compare)
            for ci, r in enumerate(selected_runs):
                with best_cols[ci]:
                    st.markdown(f"**#{runs.index(r)+1} — {r['strategy']}**")
                    st.caption(f"`{r['model']}` · temp={r['temperature']} · {r['usage']['total_tokens']} token")
                    b = get_best_alt(r)
                    st.markdown(f"*{b.get('title', '')}*")
                    st.code(b.get("code", ""), language=r.get("detected_language", "text").lower())
                    st.markdown("".join(f'<span class="improvement-tag">{x}</span>' for x in b.get("improvements", [])), unsafe_allow_html=True)

        # ═══════════════════════════════════════════════════════════════
        #  EXPORT (Markdown / PDF / JSON)
        # ═══════════════════════════════════════════════════════════════
        st.divider()
        st.subheader("📥 Exportálás")

        def generate_markdown_report(code_data, runs, judge=None):
            """Teljes Markdown riport generálása."""
            md = []
            md.append(f"# ⚡ Kódoptimalizálási riport — {code_data['name']}")
            md.append(f"\n*Generálva: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

            # Eredeti kód
            md.append("## 📝 Eredeti kód\n")
            md.append(f"```\n{code_data['code']}\n```\n")

            # Futtatások
            md.append(f"## 📊 Futtatások ({len(runs)} db)\n")
            md.append("| # | Időpont | Stratégia | Modell | Temp. | Tokenek |")
            md.append("|---|---------|-----------|--------|-------|---------|")
            for i, r in enumerate(runs):
                md.append(f"| {i+1} | {r['timestamp']} | {r['strategy']} | {r['model']} | {r['temperature']} | {r['usage']['total_tokens']} |")
            md.append("")

            # Minden futtatás részletei
            for i, r in enumerate(runs):
                md.append(f"---\n### Futtatás #{i+1} — {r['strategy']}\n")
                md.append(f"- **Modell:** {r['model']}")
                md.append(f"- **Temperature:** {r['temperature']}")
                md.append(f"- **Szempontok:** {', '.join(r['goals'])}")
                md.append(f"- **Tokenek:** {r['usage']['total_tokens']} (prompt: {r['usage']['prompt_tokens']}, válasz: {r['usage']['completion_tokens']})")
                md.append(f"- **Felismert nyelv:** {r.get('detected_language', '?')}\n")

                res = r["result"]
                md.append(f"**Elemzés:** {res.get('analysis', '—')}\n")

                # Promptok
                md.append("<details><summary>📨 Elküldött promptok</summary>\n")
                md.append(f"**System prompt:**\n```\n{r['system_prompt']}\n```\n")
                if r.get("cot_prompt"):
                    md.append(f"**CoT kiegészítés:**\n```\n{r['cot_prompt']}\n```\n")
                md.append(f"**User prompt:**\n```\n{r['user_prompt']}\n```\n")
                md.append("</details>\n")

                # Alternatívák
                for j, alt in enumerate(res.get("alternatives", [])):
                    md.append(f"#### Alternatíva #{j+1}: {alt.get('title', '')}\n")
                    md.append(f"{alt.get('explanation', '')}\n")
                    lang = r.get('detected_language', 'text').lower()
                    md.append(f"```{lang}\n{alt.get('code', '')}\n```\n")
                    imps = ", ".join(alt.get("improvements", []))
                    trds = ", ".join(alt.get("tradeoffs", []))
                    md.append(f"- ✅ **Javítások:** {imps or 'Nincs'}")
                    md.append(f"- ⚠️ **Kompromisszumok:** {trds or 'Nincs'}\n")

            # Összehasonlítás
            if len(runs) >= 2:
                md.append("---\n## 🔀 Stratégiák összehasonlítása\n")

                md.append("| Metrika |" + " | ".join(f"#{i+1} {r['strategy']}" for i, r in enumerate(runs)) + " |")
                md.append("|---------|" + " | ".join("---" for _ in runs) + " |")
                md.append("| Tokenek |" + " | ".join(str(r['usage']['total_tokens']) for r in runs) + " |")

                imp_counts = []
                for r in runs:
                    c = sum(len(a.get("improvements", [])) for a in r["result"].get("alternatives", []))
                    imp_counts.append(str(c))
                md.append("| Javítások száma |" + " | ".join(imp_counts) + " |")
                md.append("")

            # Judge eredmények
            if judge:
                md.append("---\n## 🏆 LLM-as-Judge értékelés\n")
                evals = judge.get("evaluations", [])
                winner = judge.get("winner", "?")

                md.append("| Szempont |" + " | ".join(f"#{e.get('alternative_index','?')} {e.get('title','')}" for e in evals) + " |")
                md.append("|----------|" + " | ".join("---" for _ in evals) + " |")

                for label, key in [("Teljesítmény","performance"), ("Olvashatóság","readability"), ("Biztonság","security"), ("Karbantarthatóság","maintainability"), ("**Összesített**","overall")]:
                    row = f"| {label} |"
                    for e in evals:
                        score = e.get("scores", {}).get(key, "—")
                        row += f" {score} |"
                    md.append(row)
                md.append("")

                md.append(f"**🏆 Győztes: Alternatíva #{winner}** — {judge.get('winner_reasoning', '')}\n")

                for e in evals:
                    md.append(f"**#{e.get('alternative_index','?')} — {e.get('title','')}:** {e.get('reasoning','')}")
                    md.append(f"  - Erősségek: {', '.join(e.get('strengths', []))}")
                    md.append(f"  - Gyengeségek: {', '.join(e.get('weaknesses', []))}\n")

            # Konklúzió placeholder
            md.append("---\n## 📝 Konklúzió\n")
            md.append("*[Ide írd a saját értékelésedet — melyik stratégia volt a legjobb és miért?]*\n")

            return "\n".join(md)

        def markdown_to_pdf(md_text: str) -> bytes:
            """Markdown szöveg konvertálása PDF-be fpdf2-vel."""
            try:
                from fpdf import FPDF
                import re

                # Emojik és speciális Unicode karakterek eltávolítása
                # Font keresés (Linux + Windows + Mac) — ELSŐ LÉPÉS
                font_dirs = [
                    "/usr/share/fonts/truetype/dejavu",           # Linux
                    "C:/Windows/Fonts",                            # Windows
                    "/System/Library/Fonts",                       # macOS
                    os.path.expanduser("~/.fonts"),                # User fonts
                ]

                dejavu_found = False
                regular = bold = mono = ""
                for fdir in font_dirs:
                    r_ = os.path.join(fdir, "DejaVuSans.ttf")
                    b_ = os.path.join(fdir, "DejaVuSans-Bold.ttf")
                    m_ = os.path.join(fdir, "DejaVuSansMono.ttf")
                    if os.path.exists(r_) and os.path.exists(b_) and os.path.exists(m_):
                        dejavu_found = True
                        regular, bold, mono = r_, b_, m_
                        break

                if dejavu_found:
                    body_font = "DejaVu"
                    mono_font = "DejaVuMono"
                else:
                    body_font = "Helvetica"
                    mono_font = "Courier"

                def sanitize(text):
                    if dejavu_found:
                        # DejaVu tudja a legtöbb Unicode-ot, csak az emojikat szűrjük
                        emoji_pattern = re.compile(
                            "["
                            "\U0001F600-\U0001F64F"  # emoticons
                            "\U0001F300-\U0001F5FF"  # symbols & pictographs
                            "\U0001F680-\U0001F6FF"  # transport & map
                            "\U0001F1E0-\U0001F1FF"  # flags
                            "\U00002702-\U000027B0"  # dingbats
                            "\U000024C2-\U0001F251"  # misc
                            "\U0001f926-\U0001f937"
                            "\U00010000-\U0010ffff"
                            "\u2640-\u2642"
                            "\u2600-\u2B55"           # ⚡ itt van (U+26A1)
                            "\u200d\ufe0f"
                            "]+", flags=re.UNICODE
                        )
                        text = emoji_pattern.sub("", text)
                        text = text.replace('—', '-').replace('–', '-')
                        return text
                    else:
                        # Helvetica: CSAK latin-1 kompatibilis karakterek
                        result = []
                        for ch in text:
                            try:
                                ch.encode('latin-1')
                                result.append(ch)
                            except UnicodeEncodeError:
                                # Magyar ékezetek manuális cseréje
                                replacements = {
                                    'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ö': 'o',
                                    'ő': 'o', 'ú': 'u', 'ü': 'u', 'ű': 'u',
                                    'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ö': 'O',
                                    'Ő': 'O', 'Ú': 'U', 'Ü': 'U', 'Ű': 'U',
                                    '—': '-', '–': '-', '"': '"', '"': '"',
                                    ''': "'", ''': "'", '…': '...',
                                }
                                result.append(replacements.get(ch, ''))
                        return ''.join(result)

                class PDF(FPDF):
                    def header(self):
                        self.set_font(body_font, "B", 10)
                        self.set_text_color(100, 100, 100)
                        self.cell(0, 8, sanitize("Kodoptimalizalasi riport - Prompt Engineering hazi"), align="C", new_x="LMARGIN", new_y="NEXT")
                        self.line(10, self.get_y(), 200, self.get_y())
                        self.ln(4)

                    def footer(self):
                        self.set_y(-15)
                        self.set_font(body_font, "", 8)
                        self.set_text_color(150, 150, 150)
                        self.cell(0, 10, f"Oldal {self.page_no()}/{{nb}}", align="C")

                pdf = PDF(orientation="P", unit="mm", format="A4")
                pdf.alias_nb_pages()
                pdf.set_auto_page_break(auto=True, margin=20)

                # Fontok betöltése ELSŐ add_page ELŐTT
                if dejavu_found:
                    pdf.add_font("DejaVu", "", regular, uni=True)
                    pdf.add_font("DejaVu", "B", bold, uni=True)
                    pdf.add_font("DejaVuMono", "", mono, uni=True)

                pdf.add_page()

                in_code_block = False
                code_buffer = []

                for line in md_text.split("\n"):
                    line = sanitize(line)

                    # Code block kezelés
                    if line.startswith("```"):
                        if in_code_block:
                            pdf.set_font(mono_font, "", 7)
                            pdf.set_fill_color(240, 240, 240)
                            code_text = "\n".join(code_buffer)
                            pdf.multi_cell(0, 4, code_text, fill=True)
                            pdf.ln(3)
                            code_buffer = []
                            in_code_block = False
                        else:
                            in_code_block = True
                        continue

                    if in_code_block:
                        code_buffer.append(line)
                        continue

                    # HTML tagek kihagyása
                    if line.startswith("<") and ("details" in line or "summary" in line):
                        continue

                    # Headingek
                    if line.startswith("# "):
                        pdf.set_font(body_font, "B", 18)
                        pdf.set_text_color(50, 50, 180)
                        pdf.multi_cell(0, 10, line[2:].strip())
                        pdf.ln(3)
                    elif line.startswith("## "):
                        pdf.set_font(body_font, "B", 14)
                        pdf.set_text_color(70, 70, 160)
                        pdf.multi_cell(0, 8, line[3:].strip())
                        pdf.ln(2)
                    elif line.startswith("### "):
                        pdf.set_font(body_font, "B", 12)
                        pdf.set_text_color(80, 80, 140)
                        pdf.multi_cell(0, 7, line[4:].strip())
                        pdf.ln(2)
                    elif line.startswith("#### "):
                        pdf.set_font(body_font, "B", 11)
                        pdf.set_text_color(90, 90, 120)
                        pdf.multi_cell(0, 6, line[5:].strip())
                        pdf.ln(1)
                    elif line.startswith("| "):
                        if line.startswith("|---") or line.startswith("| ---"):
                            continue
                        pdf.set_font(mono_font, "", 7)
                        pdf.set_fill_color(245, 245, 250)
                        cleaned = line.replace("**", "")
                        pdf.multi_cell(0, 4, cleaned, fill=True)
                    elif line.startswith("---"):
                        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                        pdf.ln(4)
                    elif line.startswith("- "):
                        pdf.set_font(body_font, "", 9)
                        pdf.set_text_color(30, 30, 30)
                        cleaned = line[2:].replace("**", "")
                        pdf.multi_cell(0, 5, f"  * {cleaned}")
                    elif line.strip() == "":
                        pdf.ln(2)
                    else:
                        pdf.set_font(body_font, "", 9)
                        pdf.set_text_color(30, 30, 30)
                        cleaned = line.replace("**", "").replace("*", "")
                        pdf.multi_cell(0, 5, cleaned)

                return pdf.output()

            except ImportError:
                return None
            except Exception as e:
                st.error(f"PDF generálási hiba: {e}")
                return None

        # Judge result beolvasása
        judge_data = st.session_state.get("judge_result", None)

        # Markdown generálás
        md_report = generate_markdown_report(code_data, runs, judge=judge_data)

        exp_cols = st.columns(3)
        with exp_cols[0]:
            st.download_button(
                "📄 Markdown (.md)",
                data=md_report,
                file_name=f"{code_data['name'].replace(' ', '_')}_riport.md",
                mime="text/markdown",
                use_container_width=True,
            )

        with exp_cols[1]:
            pdf_bytes = markdown_to_pdf(md_report)
            if pdf_bytes:
                st.download_button(
                    "📕 PDF (.pdf)",
                    data=pdf_bytes,
                    file_name=f"{code_data['name'].replace(' ', '_')}_riport.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            else:
                st.warning("PDF-hez: `pip install fpdf2`")

        with exp_cols[2]:
            export = db_export_code_with_runs(active_id)
            if judge_data:
                export["judge_result"] = judge_data
            st.download_button(
                "📦 JSON (nyers adat)",
                data=json.dumps(export, ensure_ascii=False, indent=2),
                file_name=f"{code_data['name'].replace(' ', '_')}_export.json",
                mime="application/json",
                use_container_width=True,
            )