import pathlib

path = pathlib.Path("app.py")
text = path.read_text(encoding="utf-8")

old = """    return FileResponse(str(index_path))

# The dashboard (static/dashboard) is served from the same origin as the"""

new = """    return FileResponse(str(index_path))


# The built dashboard's index.html references its JS/CSS bundle and icons
# with root-relative paths (e.g. /assets/index-XXXX.js, /favicon.svg), since
# that's what Vite emits by default. Serving index.html at "/" above without
# also serving these exact paths at root left the page loading successfully
# but blank, with every asset request 404ing (the /dashboard mount below
# only covers /dashboard/assets/..., not /assets/...). This makes / actually
# render, not just the /dashboard mount.
@app.get("/favicon.svg")
async def read_favicon():
    favicon_path = BASE_DIR / "static" / "dashboard" / "favicon.svg"
    if not favicon_path.exists():
        raise HTTPException(status_code=404, detail="favicon.svg not found")
    return FileResponse(str(favicon_path))


@app.get("/icons.svg")
async def read_icons():
    icons_path = BASE_DIR / "static" / "dashboard" / "icons.svg"
    if not icons_path.exists():
        raise HTTPException(status_code=404, detail="icons.svg not found")
    return FileResponse(str(icons_path))

# The dashboard (static/dashboard) is served from the same origin as the"""

count = text.count(old)
if count != 1:
    print(f"ABORTING part 1: expected 1 match, found {count}. Nothing changed.")
else:
    text = text.replace(old, new, 1)

    old2 = '    app.mount("/dashboard", StaticFiles(directory=str(STATIC_DASHBOARD_DIR), html=True), name="dashboard")'
    new2 = old2 + '''
    _dashboard_assets_dir = STATIC_DASHBOARD_DIR / "assets"
    if _dashboard_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_dashboard_assets_dir)), name="dashboard-assets-root")'''

    count2 = text.count(old2)
    if count2 != 1:
        print(f"ABORTING part 2: expected 1 match, found {count2}. Nothing changed.")
    else:
        text = text.replace(old2, new2, 1)
        path.write_text(text, encoding="utf-8")
        print("app.py patched successfully.")
