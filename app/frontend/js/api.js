/* Cliente HTTP.
 *
 * Las lecturas fallan hacia el caché; las escrituras que no salen se encolan
 * y se reintentan. El auditor nunca pierde un hallazgo por quedarse sin señal
 * en plataforma. */

const API = (() => {
  let token = null;

  const setToken = (t) => { token = t; };
  const getToken = () => token;

  class ErrorAPI extends Error {
    constructor(mensaje, codigo) { super(mensaje); this.codigo = codigo; }
  }

  async function pedir(metodo, ruta, body) {
    const opciones = { method: metodo, headers: {} };
    if (body !== undefined) {
      opciones.headers['Content-Type'] = 'application/json';
      opciones.body = JSON.stringify(body);
    }
    if (token) opciones.headers['Authorization'] = `Bearer ${token}`;

    const r = await fetch(ruta, opciones);
    const tipo = r.headers.get('Content-Type') || '';
    const datos = tipo.includes('json') ? await r.json() : await r.text();

    if (!r.ok) {
      throw new ErrorAPI((datos && datos.error) || `Error ${r.status}`, r.status);
    }
    return datos;
  }

  const get = (ruta) => pedir('GET', ruta);
  const post = (ruta, body) => pedir('POST', ruta, body);
  const put = (ruta, body) => pedir('PUT', ruta, body);
  const del = (ruta) => pedir('DELETE', ruta);

  /**
   * Mutación tolerante a la falta de red.
   *
   * Si el envío falla por conectividad, la operación queda en cola y la
   * función resuelve como `{ encolada: true }`: la UI ya aplicó el cambio
   * localmente, así que el auditor sigue trabajando sin notar la diferencia.
   * Un error del servidor (400, 403…) sí se propaga: significa que la
   * operación es inválida y reintentarla no la va a arreglar.
   */
  async function mutar(metodo, ruta, body) {
    if (!navigator.onLine) {
      const op = await Store.encolar(metodo, ruta, body);
      return { encolada: true, uuid: op.uuid };
    }
    try {
      return await pedir(metodo, ruta, body);
    } catch (e) {
      if (e instanceof ErrorAPI) throw e;   // el servidor respondió y rechazó
      const op = await Store.encolar(metodo, ruta, body);
      return { encolada: true, uuid: op.uuid };
    }
  }

  /** Lectura que cae al caché local cuando no hay red. */
  async function getCacheado(ruta, claveCache) {
    try {
      const datos = await get(ruta);
      await Store.set('meta', claveCache, datos);
      return datos;
    } catch (e) {
      const cache = await Store.get('meta', claveCache);
      if (cache) return cache;
      throw e;
    }
  }

  async function login(usuario, password) {
    const r = await post('/api/login', { usuario, password });
    setToken(r.token);
    _usuario = r.usuario;
    await Store.guardarSesion(r.token, r.usuario);
    return r;
  }

  async function logout() {
    try { await post('/api/logout'); } catch (e) { /* la sesión local se limpia igual */ }
    setToken(null);
    await Store.limpiarTodo();
  }

  // Usuario de la sesión en curso. Se cachea acá para que los módulos que no
  // reciben el usuario por parámetro (LoS, Informes) puedan consultar el rol
  // sin volver a pedirlo al servidor.
  let _usuario = null;

  /** Usuario de la sesión, o null si no hay. Sincrónico: no pega al servidor. */
  const usuarioActual = () => _usuario;
  const esAdmin = () => !!_usuario && _usuario.rol === 'admin';

  /**
   * Deja activo el token guardado en la tablet. No toca la red.
   *
   * Está separado de la validación para que el arranque pueda pedir la sesión
   * y el catálogo de sectores a la vez: el token sale del almacenamiento
   * local, así que la segunda llamada no tiene por qué esperar a la primera.
   */
  async function prepararSesion() {
    const guardada = await Store.leerSesion();
    setToken(guardada.token || null);
    _usuario = guardada.token ? (guardada.usuario || null) : null;
    return guardada;
  }

  /** Valida contra el servidor una sesión ya preparada. */
  async function validarSesion(guardada) {
    const { token: t, usuario } = guardada || await Store.leerSesion();
    if (!t) return null;

    if (!navigator.onLine) return (_usuario = usuario);   // offline: caché
    try {
      const r = await get('/api/sesion');
      return (_usuario = r.usuario);
    } catch (e) {
      if (e.codigo === 401) {               // el servidor se reinició
        setToken(null);
        await Store.limpiarSesion();
        _usuario = null;
        return null;
      }
      return (_usuario = usuario);
    }
  }

  /** Restaura el token guardado y valida que siga vigente. */
  async function restaurarSesion() {
    return validarSesion(await prepararSesion());
  }

  /**
   * Descarga un archivo generado por el backend (PDF o CSV).
   *
   * No alcanza con un <a href>: la API exige el header Authorization, que un
   * enlace no manda. Se baja como blob y se dispara la descarga desde memoria.
   */
  async function descargar(ruta, nombreSugerido) {
    const r = await fetch(ruta, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!r.ok) {
      let mensaje = `Error ${r.status}`;
      try { mensaje = (await r.json()).error || mensaje; } catch (e) { /* binario */ }
      throw new ErrorAPI(mensaje, r.status);
    }

    // El servidor manda el nombre en Content-Disposition; si el navegador no
    // lo expone (CORS), se usa el sugerido.
    const disp = r.headers.get('Content-Disposition') || '';
    const coincidencia = disp.match(/filename="?([^"]+)"?/);
    const nombre = coincidencia ? coincidencia[1] : nombreSugerido;

    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = nombre;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Se libera en el próximo tick: revocar de inmediato aborta la descarga
    // en algunos navegadores.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return nombre;
  }

  return { get, post, put, del, mutar, getCacheado, descargar, login, logout,
           restaurarSesion, prepararSesion, validarSesion,
           usuarioActual, esAdmin, setToken, getToken, ErrorAPI };
})();
