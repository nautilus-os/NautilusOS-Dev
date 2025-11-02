import base64
import mimetypes
import pathlib
import re
import urllib.parse
import urllib.request
import shutil

root = pathlib.Path(__file__).resolve().parent
index_path = root / "index.html"
output_dir = root / "NautilusOS-OneFile"
output_path = output_dir / "index.html"
cache = {}
embedded_files = {}

# Directories to embed all files from
EMBED_DIRS = [
    "v86",
    "font",
    "uv",
    "app",
    "baremux",
    "js",
    "libcurl",
    "themes",
    "testing"
]


def fetch_bytes(url):
    """Fetch remote resource and cache it"""
    if url not in cache:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            cache[url] = resp.read()
    return cache[url]


def inline_remote_css(url):
    """Inline remote CSS with embedded assets"""
    css = fetch_bytes(url).decode("utf-8")

    def repl(match):
        target = match.group(1).strip().strip("\"'")
        if target.startswith("data:"):
            return match.group(0)
        asset_url = urllib.parse.urljoin(url, target)
        data = fetch_bytes(asset_url)
        mime = mimetypes.guess_type(asset_url)[0] or "application/octet-stream"
        encoded = base64.b64encode(data).decode("ascii")
        return f"url('data:{mime};base64,{encoded}')"

    css = re.sub(r"url\(([^)]+)\)", repl, css)
    return f"<style>\n{css}\n</style>"


def inline_local_file(path):
    """Read local file as text"""
    full_path = root / path.lstrip("/")
    return full_path.read_text(encoding="utf-8")


def embed_local_css(path):
    """Embed local CSS with fonts and assets as base64"""
    full_path = root / path.lstrip("/")
    css = full_path.read_text(encoding="utf-8")
    
    def repl(match):
        target = match.group(1).strip().strip("\"'")
        if target.startswith("data:") or target.startswith("http://") or target.startswith("https://"):
            return match.group(0)
        
        # Resolve relative path
        css_dir = full_path.parent
        asset_path = (css_dir / target).resolve()
        
        if asset_path.exists() and asset_path.is_file():
            try:
                data = asset_path.read_bytes()
                mime = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
                encoded = base64.b64encode(data).decode("ascii")
                return f"url('data:{mime};base64,{encoded}')"
            except:
                return match.group(0)
        return match.group(0)
    
    css = re.sub(r"url\(([^)]+)\)", repl, css)
    return f"<style>\n{css}\n</style>"


def collect_all_files():
    """Collect all files from directories to embed - ALL as base64"""
    print("\n=== Collecting files to embed ===")
    
    for dir_name in EMBED_DIRS:
        dir_path = root / dir_name
        if not dir_path.exists():
            continue
            
        for file_path in dir_path.rglob("*"):
            if file_path.is_file():
                rel_path = "/" + str(file_path.relative_to(root)).replace("\\", "/")
                
                try:
                    # Encode ALL files as base64 to avoid any escaping issues
                    content = file_path.read_bytes()
                    encoded = base64.b64encode(content).decode("ascii")
                    embedded_files[rel_path] = {
                        "content": encoded,
                        "mime": mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                    }
                    print(f"  Embedded: {rel_path}")
                except Exception as e:
                    print(f"  Warning: Could not embed {rel_path}: {e}")
    
    # Also embed style.css from root
    style_path = root / "style.css"
    if style_path.exists():
        rel_path = "/style.css"
        content = style_path.read_bytes()
        encoded = base64.b64encode(content).decode("ascii")
        embedded_files[rel_path] = {
            "content": encoded,
            "mime": "text/css"
        }
        print(f"  Embedded: {rel_path}")


def js_string_escape(s):
    """Escape a string for safe embedding in JavaScript double quotes"""
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')


def create_virtual_fs_script():
    """Create a virtual filesystem with lazy-loaded base64 data"""
    # Create compact arrays instead of object literals
    paths = []
    mimes = []
    data_chunks = []
    
    for path, file_data in embedded_files.items():
        paths.append(path)
        mimes.append(file_data["mime"])
        data_chunks.append(file_data["content"])
    
    # Manually format JavaScript arrays to avoid repr() issues
    paths_js = "[" + ",".join(f'"{js_string_escape(p)}"' for p in paths) + "]"
    mimes_js = "[" + ",".join(f'"{js_string_escape(m)}"' for m in mimes) + "]"
    
    # Create the VFS script with data in compact format
    script = "<script>\n"
    script += "// Virtual File System - Lazy-loaded base64 data\n"
    script += "(function() {\n"
    script += f"    const paths = {paths_js};\n"
    script += f"    const mimes = {mimes_js};\n"
    script += "    const data = [\n"
    
    # Add each base64 chunk as a separate line for better parsing
    for i, chunk in enumerate(data_chunks):
        # Split very long base64 strings into smaller chunks to avoid parser issues
        if len(chunk) > 50000:
            # Split into 50KB chunks
            parts = [chunk[j:j+50000] for j in range(0, len(chunk), 50000)]
            joined = '"+\n"'.join(parts)
            script += f'        "{joined}"'
        else:
            script += f'        "{chunk}"'
        
        if i < len(data_chunks) - 1:
            script += ",\n"
        else:
            script += "\n"
    
    script += "    ];\n"
    script += """    
    // Lazy-loaded cache
    const cache = new Map();
    const blobCache = new Map();
    
    // Decode base64 to bytes (lazy)
    function getFileBytes(index) {
        if (!cache.has(index)) {
            const binary = atob(data[index]);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }
            cache.set(index, bytes);
        }
        return cache.get(index);
    }
    
    // Get blob URL for a file (for modules and workers)
    function getBlobURL(index) {
        if (!blobCache.has(index)) {
            const bytes = getFileBytes(index);
            const mime = mimes[index];
            const blob = new Blob([bytes], { type: mime });
            const url = URL.createObjectURL(blob);
            blobCache.set(index, url);
        }
        return blobCache.get(index);
    }
    
    // Build path index
    const pathIndex = new Map();
    for (let i = 0; i < paths.length; i++) {
        pathIndex.set(paths[i], i);
        // Also index without query params
        const pathNoQuery = paths[i].split('?')[0];
        if (pathNoQuery !== paths[i]) {
            pathIndex.set(pathNoQuery, i);
        }
    }
    
    // Create import map for modules
    const importMap = {
        imports: {}
    };
    
    for (let i = 0; i < paths.length; i++) {
        if (paths[i].endsWith('.mjs') || paths[i].endsWith('.js')) {
            const blobURL = getBlobURL(i);
            importMap.imports[paths[i]] = blobURL;
            importMap.imports['.' + paths[i]] = blobURL;
            // Handle absolute paths from root
            const pathWithoutLeadingSlash = paths[i].substring(1);
            importMap.imports[pathWithoutLeadingSlash] = blobURL;
        }
    }
    
    // Inject import map
    const importMapScript = document.createElement('script');
    importMapScript.type = 'importmap';
    importMapScript.textContent = JSON.stringify(importMap);
    document.head.appendChild(importMapScript);
    
    // Override fetch to serve from VFS
    const originalFetch = window.fetch;
    window.fetch = function(url, options) {
        try {
            let urlObj;
            if (typeof url === 'string') {
                urlObj = new URL(url, window.location.href);
            } else {
                urlObj = url;
            }
            
            let path = urlObj.pathname;
            // Remove query params for lookup
            const pathNoQuery = path.split('?')[0];
            
            if (pathIndex.has(path) || pathIndex.has(pathNoQuery)) {
                const index = pathIndex.get(path) || pathIndex.get(pathNoQuery);
                const bytes = getFileBytes(index);
                const mime = mimes[index];
                
                return Promise.resolve(new Response(bytes, {
                    status: 200,
                    headers: { 'Content-Type': mime }
                }));
            }
        } catch (e) {
            console.error('VFS fetch error:', e);
        }
        
        return originalFetch.apply(this, arguments);
    };
    
    // Provide files for dynamic imports and workers
    window.__nautilusGetFile__ = function(path) {
        const pathNoQuery = path.split('?')[0];
        if (!pathIndex.has(path) && !pathIndex.has(pathNoQuery)) return null;
        
        const index = pathIndex.get(path) || pathIndex.get(pathNoQuery);
        const bytes = getFileBytes(index);
        const mime = mimes[index];
        
        return new Blob([bytes], { type: mime });
    };
    
    // Expose blob URLs for modules
    window.__nautilusGetBlobURL__ = function(path) {
        const pathNoQuery = path.split('?')[0];
        if (!pathIndex.has(path) && !pathIndex.has(pathNoQuery)) return null;
        const index = pathIndex.get(path) || pathIndex.get(pathNoQuery);
        return getBlobURL(index);
    };
    
    console.log('NautilusOS Virtual File System loaded with', paths.length, 'files (lazy-loaded)');
    console.log('Import map created with', Object.keys(importMap.imports).length, 'module mappings');
})();
</script>"""
    
    return script


# Create output directory
output_dir.mkdir(parents=True, exist_ok=True)

# Collect all files to embed
collect_all_files()

# Process index.html
print("\n=== Processing index.html ===")
index_text = index_path.read_text(encoding="utf-8")

# Remove preconnect and other non-stylesheet links (useless in single file)
print("  Removing preconnect and DNS prefetch links...")
index_text = re.sub(r'<link\s+[^>]*rel=["\'](?:preconnect|dns-prefetch|prefetch)["\'][^>]*>', '', index_text, flags=re.IGNORECASE)


def replace_stylesheet(match):
    full_tag = match.group(0)
    href_match = re.search(r'href=["\']([^"\']+)["\']', full_tag)
    if not href_match:
        return full_tag
    
    href = href_match.group(1)
    if href.startswith("http://") or href.startswith("https://"):
        print(f"  Inlining remote CSS: {href}")
        return inline_remote_css(href)
    else:
        print(f"  Inlining local CSS: {href}")
        return embed_local_css(href)


# Match ALL link tags with rel="stylesheet" regardless of attribute order
link_pattern = re.compile(r'<link\s+[^>]*rel=["\']stylesheet["\'][^>]*>', re.IGNORECASE)
index_text = re.sub(link_pattern, replace_stylesheet, index_text)

# Also match stylesheet links where href comes before rel
link_pattern2 = re.compile(r'<link\s+[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\'][^>]*>', re.IGNORECASE)
def replace_stylesheet2(match):
    href = match.group(1)
    if href.startswith("http://") or href.startswith("https://"):
        print(f"  Inlining remote CSS: {href}")
        return inline_remote_css(href)
    else:
        print(f"  Inlining local CSS: {href}")
        return embed_local_css(href)
index_text = re.sub(link_pattern2, replace_stylesheet2, index_text)


def replace_script(match):
    src = match.group(1)
    if src.startswith("http://") or src.startswith("https://"):
        print(f"  Inlining remote JS: {src}")
        script_text = fetch_bytes(src).decode("utf-8")
    else:
        print(f"  Inlining local JS: {src}")
        script_text = inline_local_file(src)
    # Escape <script> and </script> tags within JavaScript to prevent premature script closing
    # This is necessary when JavaScript contains template literals with HTML content
    # Use a callback function to replace with the Unicode escape for 's'
    script_text = re.sub(r'<script', lambda m: '<\\u0073cript', script_text, flags=re.IGNORECASE)
    script_text = re.sub(r'</script>', lambda m: '<\\/script>', script_text, flags=re.IGNORECASE)
    return f"<script>\n{script_text}\n</script>"


script_pattern = re.compile(r'<script\s+[^>]*src=["\']([^"\']+)["\'][^>]*></script>', re.IGNORECASE)
index_text = re.sub(script_pattern, replace_script, index_text)

# Inject Virtual File System before </head>
print("\n=== Injecting Virtual File System ===")
vfs_script = create_virtual_fs_script()
# Only replace the last occurrence of </head> to avoid injecting into JavaScript templates
index_text = index_text.rsplit("</head>", 1)[0] + f"{vfs_script}\n</head>" + index_text.rsplit("</head>", 1)[1]

# Write output
output_path.write_text(index_text, encoding="utf-8")
file_size_mb = output_path.stat().st_size / 1024 / 1024

print(f"\n✓ Created single-file bundle: {output_path}")
print(f"✓ Embedded {len(embedded_files)} files")
print(f"✓ File size: {file_size_mb:.2f} MB")
print("\n=== Build complete! ===")
