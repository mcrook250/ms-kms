import os, uuid, datetime, socket, urllib.request, json, time, portalocker, platform
from datetime import timezone
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from pykms_Sql import sql_get_all, sql_delete_record
from pykms_DB2Dict import kmsDB2Dict

def _random_uuid():
    return str(uuid.uuid4()).replace('-', '_')

_serve_count = 0
def _increase_serve_count():
    global _serve_count
    _serve_count += 1

def _get_serve_count():
    return _serve_count

_kms_items = None
_kms_items_noglvk = None
def _get_kms_items_cache():
    global _kms_items, _kms_items_noglvk
    if _kms_items is None:
        _kms_items = {} # {group: str -> {product: str -> gvlk: str}}
        _kms_items_noglvk = 0
        for section in kmsDB2Dict():
            for element in section:
                if "KmsItems" in element:
                    for product in element["KmsItems"]:
                        group_name = product["DisplayName"]
                        items = {}
                        for item in product["SkuItems"]:
                            items[item["DisplayName"]] = item["Gvlk"]
                            if not item["Gvlk"]:
                                _kms_items_noglvk += 1
                        if len(items) == 0:
                            continue
                        if group_name not in _kms_items:
                            _kms_items[group_name] = {}
                        _kms_items[group_name].update(items)
                elif "DisplayName" in element and "BuildNumber" in element and "PlatformId" in element:
                    pass # these are WinBuilds
                elif "DisplayName" in element and "Activate" in element:
                    pass # these are CsvlkItems
                else:
                    raise NotImplementedError(f'Unknown element: {element}')
    return _kms_items, _kms_items_noglvk

def is_port_open(host, port, timeout=3):
    """
    Checks if the given host and port are open and responding.
    Uses a socket connection attempt with a timeout.
    Returns True if connection is successful, False otherwise.
    """
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except (socket.timeout, socket.error):
        return False

      
def is_auto_purge_enabled():
    """
    Checks the shared flag file to determine if auto purge is enabled.
    Returns:
        bool: True if the file exists (auto purge enabled), or False otherwise.
    """
    flag_file = '/kms/var/auto_purge_enabled'
    return os.path.exists(flag_file)

      
def get_os_info():
    """
    Retrieves information about the operating system.

    Returns:
        tuple: (os_name, distro_id, distro_version)
            - os_name (str): The name of the OS, e.g., "Linux", "Windows", "Darwin".
            - distro_id (str or None): For Linux, the distribution identifier (e.g., "ubuntu", "fedora").
              This will be None if not Linux or if detection fails.
            - distro_version (str or None): For Linux, the distribution version (e.g., "22.04"). 
              This will be None if not applicable.
    """
    os_name = platform.system()
    distro_id = None
    distro_version = None

    if os_name == "Linux":
        # Attempt to use the 'distro' module for a robust detection.
        try:
            import distro  # Ensure the module is installed (pip install distro)
            distro_id = distro.id() or None
            distro_version = distro.version() or None
        except ImportError:
            # Fallback: manually parse '/etc/os-release'
            try:
                info = {}
                with open("/etc/os-release", "r") as f:
                    for line in f:
                        if "=" in line:
                            key, value = line.strip().split("=", 1)
                            info[key] = value.strip('"')
                distro_id = info.get("ID", None)
                distro_version = info.get("VERSION_ID", None)
            except Exception:
                distro_id = None
                distro_version = None

    return os_name, distro_id, distro_version
      
      
      
def get_system_timezone():
    """
    Returns the system's local time zone info.
    """
    return datetime.datetime.now(timezone.utc).astimezone().tzinfo
      
      
def get_container_memory_usage():
    """
    Attempts to retrieve container memory usage by checking for cgroup v1 and v2.
    """
    try:
        if os.path.exists('/sys/fs/cgroup/memory/memory.limit_in_bytes'):
            # cgroup v1
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'rt') as f:
                limit = int(f.read().strip())
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'rt') as f:
                usage = int(f.read().strip())
        elif os.path.exists('/sys/fs/cgroup/memory.current'):
            # cgroup v2
            with open('/sys/fs/cgroup/memory.current', 'rt') as f:
                usage = int(f.read().strip())
            # For limits in cgroup v2 check a different file (if available)
            with open('/sys/fs/cgroup/memory.max', 'rt') as f:
                limit_val = f.read().strip()
                # 'max' means no limit set in cgroup v2
                limit = int(limit_val) if limit_val != "max" else usage * 10
        else:
            raise FileNotFoundError("No recognized cgroup memory files found.")

        # Calculate usage percentage
        usage_percent = (usage / limit * 100) if limit > 0 else 0

        return {
            "limit": limit,
            "usage": usage,
            "usage_percent": usage_percent
        }
    except Exception as e:
        return {"error": str(e)}
      

def increment_page_counter(file_path="/kms/var/page_count.txt", timeout=5):
    """
    Increments the counter in 'file_path' safely using an exclusive lock.
    If the file doesn't exist or is empty, it starts at 0.
    
    Args:
        file_path (str): Path to the counter file.
        timeout (int): How long to wait for obtaining the lock (in seconds).
        
    Returns:
        int: The updated counter value.
    """
    # Open the file in append-plus mode (creates if does not exist)
    with open(file_path, 'a+') as file:
        # Acquire an exclusive lock on the file
        portalocker.lock(file, portalocker.LOCK_EX)
        try:
            file.seek(0)
            content = file.read().strip()
            current_count = int(content) if content.isdigit() else 0
            current_count += 1

            # Move back to the beginning and truncate the file before writing
            file.seek(0)
            file.truncate()
            file.write(str(current_count))
            file.flush()  # Ensure it's written to disk
        finally:
            portalocker.unlock(file)
            
    return current_count


def read_page_counter(file_path="/kms/var/page_count.txt"):
    """
    Reads and returns the current counter value from 'file_path' without locking.
    If the file does not exist or contains non-digit content, returns 0.
    
    Args:
        file_path (str): Path to the counter file.
        
    Returns:
        int: The current counter value.
    """
    try:
        with open(file_path, 'r') as file:
            content = file.read().strip()
            return int(content) if content.isdigit() else 0
    except FileNotFoundError:
        return 0

        


# Set the refresh interval (in seconds), e.g., 3600 seconds = 1 hour
_UPDATE_INTERVAL = 3600
_last_update = 0
# Global variable to store the fetched GitHub version
_gitver_tag = None
def _get_gitver():
    """
    Makes a one-time API call to GitHub to fetch tags and stores the latest version in _gitver_tag.
    """
    global _gitver_tag, _last_update
    repo = "mcrook250/ms-kms"
    url = f"https://api.github.com/repos/{repo}/tags"
    
    try:
        with urllib.request.urlopen(url) as response:
            # Read and decode the response
            data = response.read().decode('utf-8')
            tags = json.loads(data)

        # Check if any tags were returned and store the latest (assuming the first tag is the latest)
        if tags:
            _gitver_tag = tags[0]["name"]
        else:
            _gitver_tag = "0.0.0"
    except Exception as e:
        _gitver_tag = f"Error retrieving version: {e}"
    
    _last_update = time.time()
    return _gitver_tag

  
def _GitHub_ver():
    """
    Returns the cached version. Only calls _get_gitver once.
    """
    global _gitver_tag, _last_update
    current_time = time.time()
    if _gitver_tag is None or (current_time - _last_update) > _UPDATE_INTERVAL:
        _get_gitver()
    return _gitver_tag


  
app = Flask('pykms_webui')
app.secret_key = 'my_super_secret_key_please_change'  # Replace with a strong random value
app.jinja_env.globals['start_time'] = datetime.datetime.now()
app.jinja_env.globals['get_serve_count'] = _get_serve_count
app.jinja_env.globals['random_uuid'] = _random_uuid
app.jinja_env.globals['version_info'] = None # KMS Server version
app.jinja_env.globals['rev_version'] = '0.0.0' # github version for update
app.jinja_env.globals['gui_version'] = None # WebUI server & files 'v1.3.7'
app.jinja_env.globals['rpc_health'] = None
app.jinja_env.globals['get_total_serv_count'] = read_page_counter
app.jinja_env.globals['enable_del'] = os.environ.get('ENABLE_DEL', "False")
app.jinja_env.globals['auto_purge'] = is_auto_purge_enabled()

host_to_check = 'kms'
port_to_check = 1688

_get_gitver()
app.jinja_env.globals['rev_version'] = _GitHub_ver()




_serv_version_info_path = '/kms/var/SERVERSION' # os.environ.get('PYKMS_VERSION_PATH', '../kms/var/SERVERSION')
if os.path.exists(_serv_version_info_path):
    with open(_serv_version_info_path, 'r') as f:
        app.jinja_env.globals['version_info'] = {
            'hash': f.readline().strip(),
            'branch': f.readline().strip()
        }

_version_info_path = os.environ.get('PYKMS_VERSION_PATH', '../VERSION')
if os.path.exists(_version_info_path):
    with open(_version_info_path, 'r') as j:
        app.jinja_env.globals['gui_version'] = j.readline().strip()
        

_dbEnvVarName = 'PYKMS_SQLITE_DB_PATH'
def _env_check():
    if _dbEnvVarName not in os.environ:
        raise Exception(f'Environment variable is not set: {_dbEnvVarName}')

@app.route('/')
def root():
    _increase_serve_count()
    increment_page_counter()
    error = None
    # Get the db name / path
    dbPath = None
    if _dbEnvVarName in os.environ:
        dbPath = os.environ.get(_dbEnvVarName)
    else:
        error = f'Environment variable is not set: {_dbEnvVarName}'
    # Fetch all clients from the database.
    clients = None
    try:
        if dbPath:
            clients = sql_get_all(dbPath)
    except Exception as e:
        error = f'Error while loading database: {e}'
    countClients = len(clients) if clients else 0
    noglvk = _get_kms_items_cache()
    countClientsWindows = len([c for c in clients if c['applicationId'] == 'Windows']) if clients else 0
    countClientsOffice = countClients - countClientsWindows
    port_open = is_port_open(host_to_check, port_to_check)
    app.jinja_env.globals['rpc_health'] = port_open

    return render_template(
        'clients.html',
        path='/',
        error=error,
        clients=clients,
        count_clients=countClients,
        count_clients_windows=countClientsWindows,
        count_clients_office=countClientsOffice,
        filtered=noglvk,
        count_projects=sum([len(entries) for entries in _get_kms_items_cache()[0].values()])
    ), 200 if error is None else 500

@app.route('/readyz')
def readyz():
    try:
        _env_check()
    except Exception as e:
        return f'Whooops! {e}', 503
    if (datetime.datetime.now() - app.jinja_env.globals['start_time']).seconds > 10: # Wait 10 seconds before accepting requests
        return 'OK', 200
    else:
        return 'Not ready', 503

@app.route('/livez')
def livez():
    try:
        _env_check()
        return 'OK', 200 # There are no checks for liveness, so we just return OK
    except Exception as e:
        return f'Whooops! {e}', 503

@app.route('/license')
def license():
    _increase_serve_count()
    increment_page_counter()
    with open(os.environ.get('PYKMS_LICENSE_PATH', '../LICENSE'), 'r') as f:
        return render_template(
            'license.html',
            path='/license/',
            license=f.read()
        )

@app.route('/status')
def status():
    _increase_serve_count()
    increment_page_counter()
    error = None
    # Get the db name / path
    dbPath = None
    if _dbEnvVarName in os.environ:
        dbPath = os.environ.get(_dbEnvVarName)
    else:
        error = f'Environment variable is not set: {_dbEnvVarName}'
    # Fetch all clients from the database.
    clients = None
    try:
        if dbPath:
            clients = sql_get_all(dbPath)
    except Exception as e:
        error = f'Error while loading database: {e}'
    countClients = len(clients) if clients else 0
    noglvk = _get_kms_items_cache()
    countClientsWindows = len([c for c in clients if c['applicationId'] == 'Windows']) if clients else 0
    countClientsOffice = countClients - countClientsWindows
    port_open = is_port_open(host_to_check, port_to_check)
    app.jinja_env.globals['rpc_health'] = port_open
    mem_stats = get_container_memory_usage()
    os_name, distro_id, distro_version = get_os_info()
    

    return render_template(
        'status.html',
        path='/status/',
        error=error,
        clients=clients,
        os_info=os_name,
        os_distro=distro_id,
        tz_info=get_system_timezone(),
        mem_use = f"{mem_stats['usage'] / (1024**2)*2+3:.2f} MB",
        count_clients=countClients,
        count_clients_windows=countClientsWindows,
        count_clients_office=countClientsOffice,
        filtered=noglvk,
        count_projects=sum([len(entries) for entries in _get_kms_items_cache()[0].values()])
    )
        
        
@app.route('/products')
def products():
    _increase_serve_count()
    increment_page_counter()
    items, noglvk = _get_kms_items_cache()
    countProducts = sum([len(entries) for entries in items.values()])
    countProductsWindows = sum([len(entries) for (name, entries) in items.items() if 'windows' in name.lower()])
    countProductsOffice = sum([len(entries) for (name, entries) in items.items() if 'office' in name.lower()])
    return render_template(
        'products.html',
        path='/products/',
        products=items,
        filtered=noglvk,
        count_products=countProducts,
        count_products_windows=countProductsWindows,
        count_products_office=countProductsOffice
    )
    
@app.route('/delete', methods=['POST'])
def delete_record():
    # Gate deletion on ENABLE_DEL environment variable
    enabled = os.environ.get('ENABLE_DEL', 'False').lower() in ('true', '1', 'yes')
    if not enabled:
        # 401 Unauthorized since the action isn’t permitted
        abort(401, description="Deletion endpoint is disabled by configuration")

    # Normal delete flow

    # Retrieve values from the form
    clientMachineId = request.form.get('clientMachineId')
    appId = request.form.get('applicationId')
    
    # Get the DB path from the environment variable
    dbPath = os.environ.get(_dbEnvVarName) if _dbEnvVarName in os.environ else None
    if not dbPath:
        flash(f'Environment variable is not set: {_dbEnvVarName}', 'error')
        return redirect(url_for('root'))
    
    # Attempt deletion and provide feedback
    try:
        sql_delete_record(dbPath, clientMachineId, appId)
        flash("Record deleted successfully!", 'success')
    except Exception as e:
        flash(f'Error while deleting record: {e}', 'error')
    
    # Redirect back to the main page
    return redirect(url_for('root'))
