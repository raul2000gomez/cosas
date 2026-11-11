/* Un Netlify Blobs de mentira, en memoria, para las pruebas del servidor: lo que usan las funciones (get,
   getWithMetadata, set, setJSON, delete, list) con sus escrituras condicionadas (onlyIfNew, onlyIfMatch). */

export function almacenFalso() {
  const datos = new Map(); // clave → { valor, etag }
  const ganchos = []; // { patron, hacer }: «hacer» se ejecuta una vez, justo antes de la próxima escritura cuya clave case.
  let escrituras = 0;
  const leer = (dato, opciones) => (opciones && opciones.type === 'json' ? JSON.parse(dato.valor) : dato.valor);
  const store = {
    datos,
    /** Antes de la próxima escritura de una clave que case con «patron», ejecuta «hacer» (otro que escribe entretanto). */
    antesDeEscribir(patron, hacer) {
      ganchos.push({ patron, hacer });
    },
    claves(prefijo = '') {
      return [...datos.keys()].filter((clave) => clave.startsWith(prefijo)).sort();
    },
    json(clave) {
      return datos.has(clave) ? JSON.parse(datos.get(clave).valor) : null;
    },
    async get(clave, opciones) {
      const dato = datos.get(clave);
      return dato ? leer(dato, opciones) : null;
    },
    async getWithMetadata(clave, opciones) {
      const dato = datos.get(clave);
      return dato ? { data: leer(dato, opciones), etag: dato.etag, metadata: {} } : null;
    },
    async set(clave, valor, opciones = {}) {
      const indice = ganchos.findIndex((gancho) => gancho.patron.test(clave));
      if (indice >= 0) await ganchos.splice(indice, 1)[0].hacer(clave);
      const dato = datos.get(clave);
      if (opciones.onlyIfNew && dato) return { modified: false };
      if (opciones.onlyIfMatch !== undefined && (!dato || dato.etag !== opciones.onlyIfMatch)) return { modified: false };
      escrituras += 1;
      const etag = `"e${escrituras}"`;
      datos.set(clave, { valor: String(valor), etag });
      return { modified: true, etag };
    },
    async setJSON(clave, valor, opciones) {
      return store.set(clave, JSON.stringify(valor), opciones);
    },
    async delete(clave) {
      datos.delete(clave);
    },
    async list({ prefix = '' } = {}) {
      return { blobs: store.claves(prefix).map((key) => ({ key, etag: datos.get(key).etag })), directories: [] };
    },
  };
  return store;
}
