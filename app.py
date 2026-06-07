import os
import json
import urllib.request
import sqlite3
from datetime import datetime
import uvicorn

from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="MultiPrint IS")

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
DB_FILE = "multiprint.db"
DEMO_MODE = False

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS filaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            color TEXT NOT NULL,
            weight INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS printers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ip TEXT NOT NULL,
            token TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Idle',
            progress INTEGER NOT NULL DEFAULT 0,
            filament_id INTEGER,
            temp_nozzle REAL DEFAULT 0.0,
            target_nozzle REAL DEFAULT 0.0,
            temp_bed REAL DEFAULT 0.0,
            target_bed REAL DEFAULT 0.0,
            time_remaining INTEGER DEFAULT 0,
            current_file TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            FOREIGN KEY (filament_id) REFERENCES filaments(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS print_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            priority TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Waiting',
            estimated_g INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS print_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            printer_name TEXT NOT NULL,
            status TEXT NOT NULL,
            used_weight INTEGER NOT NULL,
            date TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

ERRORS_LOG = [{"timestamp": datetime.now().strftime("%H:%M:%S"), "printer": "System", "message": "Provozní subsystém spuštěn."}]

def fetch_prusalink_data(ip: str, token: str) -> dict:
    if not ip or ip.startswith("192.168.1.5"):
        return {"status": "Offline", "progress": 0, "error_message": "Simulovaný režim."}
    url = f"http://{ip}/api/v1/status"
    req = urllib.request.Request(url, headers={"X-Api-Key": token}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=0.4) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                p_data = data.get("printer", {})
                j_data = data.get("job", {})
                e_data = data.get("error", {})
                state = p_data.get("state", "IDLE").capitalize()
                progress = int(j_data.get("progress", 0)) if state == "Printing" else 0
                
                file_data = j_data.get("file")
                if not isinstance(file_data, dict):
                    file_data = {}
                file_name = file_data.get("display_name") or file_data.get("name") or ""

                return {
                    "status": state, "progress": progress,
                    "temp_nozzle": p_data.get("temp_nozzle", 0.0), "target_nozzle": p_data.get("target_nozzle", 0.0),
                    "temp_bed": p_data.get("temp_bed", 0.0), "target_bed": p_data.get("target_bed", 0.0),
                    "time_remaining": j_data.get("time_remaining", 0), "current_file": file_name,
                    "error_message": e_data.get("message", "")
                }
    except Exception:
        return {"status": "Offline", "progress": 0, "error_message": "Zařízení neodpovídá."}
    return {"status": "Offline", "progress": 0}

def render_tab_context(request: Request, active_tab: str):
    conn = get_db()
    printers_raw = conn.execute("SELECT * FROM printers").fetchall()
    printers = [dict(p) for p in printers_raw]
    
    for p in printers:
        if p["ip"] not in ["192.168.1.50", "192.168.1.51"]:
            api = fetch_prusalink_data(p["ip"], p["token"])
            if api:
                current_file_val = api.get("current_file", "")
                if api.get("status") == "Printing" and not current_file_val:
                    current_file_val = p.get("current_file", "")
                elif api.get("status") != "Printing":
                    current_file_val = ""

                conn.execute("""
                    UPDATE printers SET 
                        status = ?, progress = ?, temp_nozzle = ?, target_nozzle = ?, 
                        temp_bed = ?, target_bed = ?, time_remaining = ?, current_file = ?, error_message = ?
                    WHERE id = ?
                """, (
                    api.get("status", "Offline"), api.get("progress", 0), api.get("temp_nozzle", 0.0),
                    api.get("target_nozzle", 0.0), api.get("temp_bed", 0.0), api.get("target_bed", 0.0),
                    api.get("time_remaining", 0), current_file_val, api.get("error_message", ""), p["id"]
                ))
    conn.commit()

    printers = [dict(p) for p in conn.execute("SELECT * FROM printers").fetchall()]
    filaments = [dict(f) for f in conn.execute("SELECT * FROM filaments").fetchall()]
    print_queue = [dict(q) for q in conn.execute("SELECT * FROM print_queue").fetchall()]
    print_history = [dict(h) for h in conn.execute("SELECT * FROM print_history ORDER BY id DESC").fetchall()]
    conn.close()

    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "request": request,
            "active_tab": active_tab,
            "demo_mode": DEMO_MODE,
            "theme": request.cookies.get("theme", "light"),
            "printers": printers,
            "filaments": filaments,
            "print_queue": print_queue,
            "print_history": print_history,
            "errors_log": ERRORS_LOG,
            "total_p": len(printers),
            "printing_p": sum(1 for p in printers if p["status"] == "Printing"),
            "idle_p": sum(1 for p in printers if p["status"] == "Idle"),
            "error_p": sum(1 for p in printers if p["status"] in ["Offline", "Error"])
        }
    )

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return render_tab_context(request, "dashboard")

@app.get("/tab/{tab_name}", response_class=HTMLResponse)
async def switch_tab(request: Request, tab_name: str):
    return render_tab_context(request, tab_name)

@app.get("/api/printer/{p_id}")
async def get_printer_api_details(p_id: int):
    with get_db() as conn:
        p = conn.execute("SELECT * FROM printers WHERE id = ?", (p_id,)).fetchone()
        if p: return JSONResponse(content=dict(p))
    return JSONResponse(content={"error": "Not found"}, status_code=404)

@app.post("/printer/add")
async def add_printer(name: str = Form(...), ip: str = Form(...), token: str = Form(...)):
    with get_db() as conn:
        conn.execute("INSERT INTO printers (name, ip, token, status, progress, filament_id) VALUES (?, ?, ?, 'Idle', 0, NULL)", (name, ip, token))
        conn.commit()
    return RedirectResponse(url="/tab/printers", status_code=303)

@app.post("/filament/add")
async def add_filament(type: str = Form(...), color: str = Form(...), weight: int = Form(...)):
    with get_db() as conn:
        conn.execute("INSERT INTO filaments (type, color, weight) VALUES (?, ?, ?)", (type, color, weight))
        conn.commit()
    return RedirectResponse(url="/tab/filaments", status_code=303)

@app.post("/printer/load-filament")
async def load_filament(printer_id: int = Form(...), filament_id: str = Form(None)):
    with get_db() as conn:
        printer = conn.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if printer and printer["status"] == "Printing": return RedirectResponse(url="/", status_code=303)
        if filament_id == "REMOVE":
            conn.execute("UPDATE printers SET filament_id = NULL WHERE id = ?", (printer_id,))
        else:
            fid = int(filament_id) if (filament_id and filament_id.isdigit()) else None
            conn.execute("UPDATE printers SET filament_id = ? WHERE id = ?", (fid, printer_id))
        conn.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/queue/add")
async def add_to_queue(name: str = Form(...), priority: str = Form(...), estimated_g: int = Form(...)):
    with get_db() as conn:
        conn.execute("INSERT INTO print_queue (name, priority, status, estimated_g) VALUES (?, ?, 'Waiting', ?)", (name, priority, estimated_g))
        conn.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/queue/start")
async def start_job(job_id: int = Form(...), printer_id: int = Form(...)):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM print_queue WHERE id = ?", (job_id,)).fetchone()
        printer = conn.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if job and printer and printer["status"] == "Idle":
            filament = conn.execute("SELECT * FROM filaments WHERE id = ?", (printer["filament_id"],)).fetchone() if printer["filament_id"] else None
            if not filament: return RedirectResponse(url="/", status_code=303)
            new_weight = max(0, filament["weight"] - job["estimated_g"])
            conn.execute("UPDATE filaments SET weight = ? WHERE id = ?", (new_weight, filament["id"]))
            conn.execute("UPDATE printers SET status = 'Printing', progress = 1, current_file = ?, time_remaining = 3600 WHERE id = ?", (job["name"], printer_id))
            conn.execute("INSERT INTO print_history (filename, printer_name, status, used_weight, date) VALUES (?, ?, 'Success', ?, ?)", (job["name"], printer["name"], job["estimated_g"], datetime.now().strftime("%d. %m. %Y")))
            conn.execute("DELETE FROM print_queue WHERE id = ?", (job_id,))
            conn.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/printer/delete/{p_id}")
async def delete_printer(p_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM printers WHERE id = ?", (p_id,))
        conn.commit()
    return RedirectResponse(url="/tab/printers", status_code=303)

@app.get("/filament/delete/{f_id}")
async def delete_filament(f_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM filaments WHERE id = ?", (f_id,))
        conn.execute("UPDATE printers SET filament_id = NULL WHERE filament_id = ?", (f_id,))
        conn.commit()
    return RedirectResponse(url="/tab/filaments", status_code=303)

@app.post("/settings/demo")
async def toggle_demo(demo_active: str = Form(None)):
    global DEMO_MODE
    with get_db() as conn:
        # Vyčistění databáze
        conn.execute("DELETE FROM printers")
        conn.execute("DELETE FROM filaments")
        conn.execute("DELETE FROM print_queue")
        conn.execute("DELETE FROM print_history")
        
        if demo_active == "on":
            DEMO_MODE = True
            # Vložení ukázkových filamentů
            conn.execute("INSERT INTO filaments (id, type, color, weight) VALUES (1, 'PLA', 'Prusament Orange', 850)")
            conn.execute("INSERT INTO filaments (id, type, color, weight) VALUES (2, 'PETG', 'Galaxy Black', 1000)")
            
            # Vložení ukázkových tiskáren
            conn.execute("""
                INSERT INTO printers (id, name, ip, token, status, progress, filament_id, temp_nozzle, target_nozzle, temp_bed, target_bed, time_remaining, current_file)
                VALUES (1, 'Prusa MK3S+ Alpha', '192.168.1.50', 'demo', 'Printing', 68, 1, 215.0, 215.0, 60.0, 60.0, 1450, 'benchy.gcode')
            """)
            conn.execute("""
                INSERT INTO printers (id, name, ip, token, status, progress, filament_id, temp_nozzle, target_nozzle, temp_bed, target_bed, time_remaining, current_file)
                VALUES (2, 'Prusa MK4 Beta', '192.168.1.51', 'demo', 'Idle', 0, 2, 28.0, 0.0, 27.0, 0.0, 0, '')
            """)
            conn.execute("INSERT INTO printers (id, name, ip, token, status, progress, filament_id) VALUES (3, 'Prusa XL Gamma', '192.168.1.52', 'demo', 'Offline', 0, NULL)")
            
            # Fronta a historie
            conn.execute("INSERT INTO print_queue (name, priority, status, estimated_g) VALUES ('shield.gcode', 'High', 'Waiting', 45)")
            conn.execute("INSERT INTO print_queue (name, priority, status, estimated_g) VALUES ('enclosure_corner.gcode', 'Medium', 'Waiting', 120)")
            conn.execute("INSERT INTO print_history (filename, printer_name, status, used_weight, date) VALUES ('pikachu.gcode', 'Prusa MK3S+ Alpha', 'Success', 35, ?)", (datetime.now().strftime("%d. %m. %Y"),))
        else:
            DEMO_MODE = False
        conn.commit()
    return RedirectResponse(url="/tab/settings", status_code=303)

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)