root@mysportsnest-backend:/var/www/mysportsnest# git pull origin main
From github.com:abdurrakibrafi/marCo_upwork
 * branch            main       -> FETCH_HEAD
Already up to date.
root@mysportsnest-backend:/var/www/mysportsnest# docker restart mysportsnest_backend
mysportsnest_backend
root@mysportsnest-backend:/var/www/mysportsnest# docker exec -it mysportsnest_backend python -c "
import requests
r = requests.get('http://127.0.0.1:8000/api/entities/league/131837/standings/')
print('HTTP Status:', r.status_code)
data = r.json()
print('Source:', data.get('source'))
print('Structure:', data.get('structure'))
print('Conferences:', list(data.get('conferences', {}).keys()))
if data.get('standings'):
    s0 = data['standings'][0]
    print('Sample Team:', s0.get('team_name'), '| Conference:', s0.get('conference'), '| PCT:', s0.get('win_pct'))
"
/usr/local/lib/python3.13/site-packages/requests/__init__.py:113: RequestsDependencyWarning: urllib3 (2.6.3) or chardet (7.6.0)/charset_normalizer (3.4.4) doesn't match a supported version!
  warnings.warn(
Traceback (most recent call last):
  File "/usr/local/lib/python3.13/site-packages/urllib3/connection.py", line 204, in _new_conn
    sock = connection.create_connection(
        (self._dns_host, self.port),
    ...<2 lines>...
        socket_options=self.socket_options,
    )
  File "/usr/local/lib/python3.13/site-packages/urllib3/util/connection.py", line 85, in create_connection
    raise err
  File "/usr/local/lib/python3.13/site-packages/urllib3/util/connection.py", line 73, in create_connection
    sock.connect(sa)
    ~~~~~~~~~~~~^^^^
ConnectionRefusedError: [Errno 111] Connection refused

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/usr/local/lib/python3.13/site-packages/urllib3/connectionpool.py", line 787, in urlopen
    response = self._make_request(
        conn,
    ...<10 lines>...
        **response_kw,
    )
  File "/usr/local/lib/python3.13/site-packages/urllib3/connectionpool.py", line 493, in _make_request
    conn.request(
    ~~~~~~~~~~~~^
        method,
        ^^^^^^^
    ...<6 lines>...
        enforce_content_length=enforce_content_length,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/usr/local/lib/python3.13/site-packages/urllib3/connection.py", line 500, in request
    self.endheaders()
    ~~~~~~~~~~~~~~~^^
  File "/usr/local/lib/python3.13/http/client.py", line 1353, in endheaders
    self._send_output(message_body, encode_chunked=encode_chunked)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/http/client.py", line 1113, in _send_output
    self.send(msg)
    ~~~~~~~~~^^^^^
  File "/usr/local/lib/python3.13/http/client.py", line 1057, in send
    self.connect()
    ~~~~~~~~~~~~^^
  File "/usr/local/lib/python3.13/site-packages/urllib3/connection.py", line 331, in connect
    self.sock = self._new_conn()
                ~~~~~~~~~~~~~~^^
  File "/usr/local/lib/python3.13/site-packages/urllib3/connection.py", line 219, in _new_conn
    raise NewConnectionError(
        self, f"Failed to establish a new connection: {e}"
    ) from e
urllib3.exceptions.NewConnectionError: HTTPConnection(host='127.0.0.1', port=8000): Failed to establish a new connection: [Errno 111] Connection refused

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/usr/local/lib/python3.13/site-packages/requests/adapters.py", line 644, in send
    resp = conn.urlopen(
        method=request.method,
    ...<9 lines>...
        chunked=chunked,
    )
  File "/usr/local/lib/python3.13/site-packages/urllib3/connectionpool.py", line 841, in urlopen
    retries = retries.increment(
        method, url, error=new_e, _pool=self, _stacktrace=sys.exc_info()[2]
    )
  File "/usr/local/lib/python3.13/site-packages/urllib3/util/retry.py", line 535, in increment
    raise MaxRetryError(_pool, url, reason) from reason  # type: ignore[arg-type]
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
urllib3.exceptions.MaxRetryError: HTTPConnectionPool(host='127.0.0.1', port=8000): Max retries exceeded with url: /api/entities/league/131837/standings/ (Caused by NewConnectionError("HTTPConnection(host='127.0.0.1', port=8000): Failed to establish a new connection: [Errno 111] Connection refused"))

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "<string>", line 3, in <module>
    r = requests.get('http://127.0.0.1:8000/api/entities/league/131837/standings/')
  File "/usr/local/lib/python3.13/site-packages/requests/api.py", line 73, in get
    return request("get", url, params=params, **kwargs)
  File "/usr/local/lib/python3.13/site-packages/requests/api.py", line 59, in request
    return session.request(method=method, url=url, **kwargs)
           ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/requests/sessions.py", line 589, in request
    resp = self.send(prep, **send_kwargs)
  File "/usr/local/lib/python3.13/site-packages/requests/sessions.py", line 703, in send
    r = adapter.send(request, **kwargs)
  File "/usr/local/lib/python3.13/site-packages/requests/adapters.py", line 677, in send
    raise ConnectionError(e, request=request)
requests.exceptions.ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=8000): Max retries exceeded with url: /api/entities/league/131837/standings/ (Caused by NewConnectionError("HTTPConnection(host='127.0.0.1', port=8000): Failed to establish a new connection: [Errno 111] Connection refused"))
root@mysportsnest-backend:/var/www/mysportsnest#