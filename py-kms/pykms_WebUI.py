import os, uuid, datetime, socket, urllib.request, json
from packaging import version
from flask import Flask, render_template
from pykms_Sql import sql_get_all
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


  
app = Flask('pykms_webui')
app.jinja_env.globals['start_time'] = datetime.datetime.now()
app.jinja_env.globals['get_serve_count'] = _get_serve_count
app.jinja_env.globals['random_uuid'] = _random_uuid
app.jinja_env.globals['version_info'] = None # KMS Server version
app.jinja_env.globals['rev_version'] = '0.0.0' # github version for update
app.jinja_env.globals['gui_version'] = None # WebUI server & files 'v1.3.7'
app.jinja_env.globals['rpc_health'] = None

host_to_check = 'kms'
port_to_check = 1688

repo = "mcrook250/ms-kms"
url = f"https://api.github.com/repos/{repo}/tags"

with urllib.request.urlopen(url) as response:
    data = response.read()
    tags = json.loads(data)

if tags:
    latest_version = tags[0]["name"]  # Assuming the first tag is the latest
    app.jinja_env.globals['rev_version'] = latest_version
else:
    app.jinja_env.globals['rev_version'] = "0.0.0"


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
    with open(os.environ.get('PYKMS_LICENSE_PATH', '../LICENSE'), 'r') as f:
        return render_template(
            'license.html',
            path='/license/',
            license=f.read()
        )

@app.route('/status')
def status():
    _increase_serve_count()
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
        'status.html',
        path='/status/',
        error=error,
        clients=clients,
        count_clients=countClients,
        count_clients_windows=countClientsWindows,
        count_clients_office=countClientsOffice,
        filtered=noglvk,
        count_projects=sum([len(entries) for entries in _get_kms_items_cache()[0].values()])
    )
        
        
@app.route('/products')
def products():
    _increase_serve_count()
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
    