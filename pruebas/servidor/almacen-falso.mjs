/* Un Netlify Blobs de mentira, en memoria, para las pruebas del servidor: lo que usan las funciones (get,
   getWithMetadata, set, setJSON, delete, list) con sus escrituras condicionadas (onlyIfNew, onlyIfMatch). Como el de
   verdad, borra sin comparar. */

export function almacenFalso() {
  const datos = new Map(); // clave → { valor, etag }
  const ganchos = []; // { patron, hacer }: «hacer» se ejecuta una vez, justo antes de la próxima escritura cuya clave case.
  const ganchosDeBorrado = []; // Lo mismo, justo antes del próximo borrado.
  /** Ejecuta (y gasta) el primer gancho de esa lista que case con la clave. */
  const disparar = async (lista, clave) => {
    const indice = lista.findIndex((gancho) => gancho.patron.test(clave));
    if (indice >= 0) await lista.splice(indice, 1)[0].hacer(clave);
  };
  let escrituras = 0;
  const leer = (dato, opciones) => (opciones && opciones.type === 'json' ? JSON.parse(dato.valor) : dato.valor);
  const store = {
    datos,
    /** Antes de la próxima escritura de una clave que case con «patron», ejecuta «hacer» (otro que escribe entretanto). */
    antesDeEscribir(patron, hacer) {
      ganchos.push({ patron, hacer });
    },
    /** Antes del próximo borrado de una clave que case con «patron», ejecuta «hacer» (lo que pasa justo antes de que desaparezca). */
    antesDeBorrar(patron, hacer) {
      ganchosDeBorrado.push({ patron, hacer });
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
      await disparar(ganchos, clave);
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
      await disparar(ganchosDeBorrado, clave);
      datos.delete(clave);
    },
    async list({ prefix = '' } = {}) {
      return { blobs: store.claves(prefix).map((key) => ({ key, etag: datos.get(key).etag })), directories: [] };
    },
  };
  return store;
}
