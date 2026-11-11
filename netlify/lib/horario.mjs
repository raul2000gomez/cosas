/* Cosas · recordatorios: cuándo toca avisar.

   Un recordatorio dice una hora de reloj y cada cuánto (siempre se repite, hasta que la cosa se da por hecha):
     { hora: 'HH:MM', frec: 'dia' }                  cada día
     { hora: 'HH:MM', frec: 'laborables' }           de lunes a viernes
     { hora: 'HH:MM', frec: 'semana', dia: 0..6 }    cada semana, ese día (0 = domingo, como Date)
     { hora: 'HH:MM', frec: 'semana', dias: [0..6] } cada semana, esos días (dos o más, sin repetir, de menor a mayor)
     { hora: 'HH:MM', frec: 'mes', diaMes: 1..31 }   cada mes, ese día (o el último, si el mes no llega)

   La hora es la del reloj de quien lo puso: su zona horaria (IANA, «Europe/Madrid») viaja con sus recordatorios y
   aquí se calcula el instante, con los cambios de hora incluidos. Sin dependencias: Intl hace las cuentas. */

const HORA = /^([01]\d|2[0-3]):([0-5]\d)$/;
const FRECUENCIAS = ['dia', 'laborables', 'semana', 'mes'];
const DIAS_DE_BUSQUEDA = 370; // Un año largo: de sobra para dar con el siguiente día que toca.

const esEntero = (v, minimo, maximo) => Number.isInteger(v) && v >= minimo && v <= maximo;

/** Los días que tiene ese mes (mes de 1 a 12). */
export const diasDelMes = (anio, mes) => new Date(Date.UTC(anio, mes, 0)).getUTCDate();

/**
 * Los días de un recordatorio semanal, sin repetir y de menor a mayor: los de «dias» (una lista) o, sin ella, el de
 * «dia»; null si alguno no es un día de la semana o no hay ninguno.
 */
function diasDeLaSemana(bruto) {
  const lista = Array.isArray(bruto.dias) ? bruto.dias : [bruto.dia];
  if (lista.length === 0 || lista.length > 7 || !lista.every((dia) => esEntero(dia, 0, 6))) return null;
  return [...new Set(lista)].sort((a, b) => a - b);
}

/**
 * El recordatorio con su forma exacta (solo sus campos, siempre en el mismo orden: así dos iguales dan el mismo
 * JSON), o null si no es válido. Uno semanal de un solo día lleva «dia», aunque llegue como una lista de uno: es la
 * forma de siempre, la que entienden también las versiones de la app de antes de poder elegir varios.
 */
export function recordatorioDe(bruto) {
  if (!bruto || typeof bruto !== 'object' || typeof bruto.hora !== 'string' || !HORA.test(bruto.hora) || !FRECUENCIAS.includes(bruto.frec)) return null;
  const { hora, frec } = bruto;
  if (frec === 'semana') {
    const dias = diasDeLaSemana(bruto);
    if (!dias) return null;
    return dias.length === 1 ? { hora, frec, dia: dias[0] } : { hora, frec, dias };
  }
  if (frec === 'mes') return esEntero(bruto.diaMes, 1, 31) ? { hora, frec, diaMes: bruto.diaMes } : null;
  return { hora, frec };
}

const formatos = new Map(); // Un formateador por zona: crearlos es lo caro.
// Cuántos se guardan como mucho. Zonas hay unos pocos cientos, pero cada una se puede escribir de mil maneras
// («europe/madrid», «EUROPE/Madrid»: todas valen) y cada formateador ocupa decenas de KB: sin tope, quien enviara
// una distinta cada vez llenaría la memoria de la función. Al llegar al tope se empieza de cero.
export const MAX_FORMATOS = 300;

function formatoDe(zona) {
  let formato = formatos.get(zona);
  if (!formato) {
    formato = new Intl.DateTimeFormat('en-US', {
      timeZone: zona, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric',
    });
    if (formatos.size >= MAX_FORMATOS) formatos.clear();
    formatos.set(zona, formato);
  }
  return formato;
}

/** ¿Es una zona horaria que Intl conoce? */
export function esZona(zona) {
  if (typeof zona !== 'string' || zona === '' || zona.length > 64) return false;
  try {
    formatoDe(zona);
    return true;
  } catch (error) {
    return false;
  }
}

/** Lo que marca el reloj de esa zona en ese instante: { anio, mes (1-12), dia, hora, minuto, segundo }. */
export function relojEn(zona, instante) {
  const reloj = {};
  for (const { type, value } of formatoDe(zona).formatToParts(new Date(instante))) {
    if (type !== 'literal') reloj[type] = Number(value);
  }
  return { anio: reloj.year, mes: reloj.month, dia: reloj.day, hora: reloj.hour, minuto: reloj.minute, segundo: reloj.second };
}

/** Lo que el reloj de esa zona va por delante de UTC en ese instante, en milisegundos. */
function desfase(zona, instante) {
  const r = relojEn(zona, instante);
  return Date.UTC(r.anio, r.mes - 1, r.dia, r.hora, r.minuto, r.segundo) - Math.floor(instante / 1000) * 1000;
}

/**
 * El instante en que el reloj de esa zona marca ese día y esa hora. Si esa hora no existe (el salto de primavera se
 * la come), el aviso llega en cuanto pasa el salto, con el reloj una hora más allá; si existe dos veces (otoño), la primera.
 */
export function instanteDe(zona, anio, mes, dia, hora, minuto) {
  const reloj = Date.UTC(anio, mes - 1, dia, hora, minuto);
  const antes = reloj - desfase(zona, reloj - 24 * 3600 * 1000); // Con el desfase de un día antes…
  const despues = reloj - desfase(zona, reloj + 24 * 3600 * 1000); // …y con el de un día después.
  const marca = (instante) => {
    const r = relojEn(zona, instante);
    return r.dia === dia && r.hora === hora && r.minuto === minuto;
  };
  const candidatos = [antes, despues].sort((a, b) => a - b);
  for (const candidato of candidatos) if (marca(candidato)) return candidato;
  return candidatos[1]; // La hora que no existe: el más tardío cae justo después del salto.
}

function tocaEseDia(r, anio, mes, dia, diaSemana) {
  if (r.frec === 'laborables') return diaSemana >= 1 && diaSemana <= 5;
  if (r.frec === 'semana') return r.dias ? r.dias.includes(diaSemana) : diaSemana === r.dia;
  if (r.frec === 'mes') return dia === Math.min(r.diaMes, diasDelMes(anio, mes));
  return true; // Cada día.
}

/**
 * El próximo instante, estrictamente posterior a «desde», en que toca ese recordatorio (ya validado) en esa zona.
 * Siempre hay uno (null solo si en más de un año no apareciera, que no pasa con un recordatorio válido).
 */
export function proximoAviso(r, zona, desde) {
  const [hora, minuto] = r.hora.split(':').map(Number);
  const hoy = relojEn(zona, desde);
  for (let i = 0; i <= DIAS_DE_BUSQUEDA; i += 1) {
    const fecha = new Date(Date.UTC(hoy.anio, hoy.mes - 1, hoy.dia + i)); // Solo calendario: días enteros, sin horas.
    const [anio, mes, dia] = [fecha.getUTCFullYear(), fecha.getUTCMonth() + 1, fecha.getUTCDate()];
    if (!tocaEseDia(r, anio, mes, dia, fecha.getUTCDay())) continue;
    const instante = instanteDe(zona, anio, mes, dia, hora, minuto);
    if (instante > desde) return instante;
  }
  return null;
}

/** El minuto (UTC) de un instante, como clave que se ordena sola: «AAAAMMDDHHmm». */
export function minutoDe(instante) {
  const f = new Date(instante);
  const dos = (n) => String(n).padStart(2, '0');
  return `${f.getUTCFullYear()}${dos(f.getUTCMonth() + 1)}${dos(f.getUTCDate())}${dos(f.getUTCHours())}${dos(f.getUTCMinutes())}`;
}

/** El día (UTC) de un instante, «AAAAMMDD». */
export const diaDe = (instante) => minutoDe(instante).slice(0, 8);
