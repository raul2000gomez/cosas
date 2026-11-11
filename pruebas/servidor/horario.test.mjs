/* Pruebas de netlify/lib/horario.mjs:  node --test --experimental-test-module-mocks pruebas/servidor/*.test.mjs */
import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import { MAX_FORMATOS, diaDe, diasDelMes, esZona, instanteDe, minutoDe, proximoAviso, recordatorioDe, relojEn } from '../../netlify/lib/horario.mjs';

const MADRID = 'Europe/Madrid';
const utc = (texto) => Date.parse(`${texto}Z`);
const iso = (instante) => (instante === null ? null : new Date(instante).toISOString().slice(0, 16));

test('recordatorioDe: solo lo válido, con sus campos justos y en orden', () => {
  assert.deepEqual(recordatorioDe({ frec: 'dia', hora: '09:00', sobra: 1, dia: 3 }), { hora: '09:00', frec: 'dia' });
  assert.equal(JSON.stringify(recordatorioDe({ dia: 2, frec: 'semana', hora: '07:05' })), '{"hora":"07:05","frec":"semana","dia":2}');
  assert.deepEqual(recordatorioDe({ hora: '23:59', frec: 'mes', diaMes: 31 }), { hora: '23:59', frec: 'mes', diaMes: 31 });
  assert.deepEqual(recordatorioDe({ hora: '10:30', frec: 'laborables' }), { hora: '10:30', frec: 'laborables' });
  for (const malo of [null, 'x', 7, [], {}, { hora: '9:00', frec: 'dia' }, { hora: '24:00', frec: 'dia' }, { hora: '09:60', frec: 'dia' }, { hora: '09:00' },
    { hora: '09:00', frec: 'hora' }, { hora: '09:00', frec: 'semana' }, { hora: '09:00', frec: 'semana', dia: 7 }, { hora: '09:00', frec: 'semana', dia: 1.5 },
    { hora: '09:00', frec: 'mes', diaMes: 0 }, { hora: '09:00', frec: 'mes', diaMes: 32 }, { hora: '09:00', frec: 'mes', diaMes: '10' }, { hora: 900, frec: 'dia' }]) {
    assert.equal(recordatorioDe(malo), null, JSON.stringify(malo));
  }
});

test('recordatorioDe: «una vez» ya no existe (un recordatorio siempre se repite)', () => {
  assert.equal(recordatorioDe({ hora: '09:00', frec: 'una', fecha: '2026-11-12' }), null);
  assert.equal(recordatorioDe({ hora: '09:00', frec: 'una' }), null);
  // Y una fecha que venga de más no convierte en otra cosa a uno que sí vale.
  assert.deepEqual(recordatorioDe({ hora: '09:00', frec: 'dia', fecha: '2026-11-12' }), { hora: '09:00', frec: 'dia' });
});

test('zonas: las que Intl conoce', () => {
  for (const zona of [MADRID, 'UTC', 'America/New_York', 'Asia/Kolkata', 'Pacific/Auckland']) assert.equal(esZona(zona), true, zona);
  for (const zona of ['', 'Marte/Olimpo', null, 42, 'x'.repeat(65)]) assert.equal(esZona(zona), false, String(zona));
});

test('zonas: los formateadores se reutilizan, pero no se guardan sin fin (una zona se puede escribir de mil maneras)', () => {
  const DeVerdad = Intl.DateTimeFormat;
  let creados = 0;
  mock.method(Intl, 'DateTimeFormat', function contado(...argumentos) {
    creados += 1;
    return new DeVerdad(...argumentos);
  });
  try {
    // «Europe/Madrid» con las mayúsculas cambiadas de sitio: todas valen, y cada una es otra para la lista. (La
    // última letra, siempre en mayúscula: así ninguna es una de las que ya han usado las pruebas de arriba.)
    const letras = 'europemadri';
    const variante = (n) => `${[...letras].map((letra, i) => ((n >> i) & 1 ? letra.toUpperCase() : letra)).join('').replace(/^(.{6})/, '$1/')}D`;
    assert.equal(variante(1), 'Europe/madriD');
    assert.equal(esZona(variante(1)), true);
    const trasLaPrimera = creados;
    assert.deepEqual(relojEn(variante(1), utc('2026-07-01T10:00')), relojEn(MADRID, utc('2026-07-01T10:00')));
    assert.equal(esZona(variante(1)), true);
    assert.equal(creados, trasLaPrimera); // La misma, otra vez: no se crea otro.
    // Muchas más de las que se guardan: todas se atienden, y la primera ya no está guardada (hubo que hacer sitio).
    for (let n = 2; n < 2 * MAX_FORMATOS + 2; n += 1) assert.equal(esZona(variante(n)), true, variante(n));
    const trasTodas = creados;
    assert.equal(trasTodas - trasLaPrimera, 2 * MAX_FORMATOS);
    assert.equal(esZona(variante(1)), true);
    assert.equal(creados, trasTodas + 1);
    // Y las cuentas siguen saliendo, se haya vaciado la lista o no.
    assert.equal(iso(proximoAviso({ hora: '09:00', frec: 'dia' }, MADRID, utc('2026-11-10T07:00'))), '2026-11-10T08:00');
  } finally {
    mock.restoreAll();
  }
});

test('relojEn e instanteDe: ida y vuelta, también con medias horas', () => {
  assert.deepEqual(relojEn(MADRID, utc('2026-11-10T08:00')), { anio: 2026, mes: 11, dia: 10, hora: 9, minuto: 0, segundo: 0 });
  assert.equal(iso(instanteDe(MADRID, 2026, 11, 10, 9, 0)), '2026-11-10T08:00');
  assert.equal(iso(instanteDe(MADRID, 2026, 7, 10, 9, 0)), '2026-07-10T07:00');
  assert.equal(iso(instanteDe('Asia/Kolkata', 2026, 11, 10, 9, 0)), '2026-11-10T03:30');
  assert.equal(iso(instanteDe('America/New_York', 2026, 11, 10, 23, 30)), '2026-11-11T04:30');
  assert.equal(iso(instanteDe('Pacific/Auckland', 2026, 1, 1, 0, 0)), '2025-12-31T11:00');
});

test('cambios de hora: la hora que no existe llega tras el salto; la que existe dos veces, la primera', () => {
  // Madrid, 29 de marzo de 2026: a las 02:00 son las 03:00 (01:00 UTC). Las 02:30 no existen.
  assert.equal(iso(instanteDe(MADRID, 2026, 3, 29, 1, 59)), '2026-03-29T00:59');
  assert.equal(iso(instanteDe(MADRID, 2026, 3, 29, 3, 0)), '2026-03-29T01:00');
  assert.equal(iso(instanteDe(MADRID, 2026, 3, 29, 2, 30)), '2026-03-29T01:30'); // Con el reloj en las 03:30.
  // Madrid, 25 de octubre de 2026: a las 03:00 vuelven a ser las 02:00 (01:00 UTC). Las 02:30 existen dos veces.
  assert.equal(iso(instanteDe(MADRID, 2026, 10, 25, 2, 30)), '2026-10-25T00:30');
  assert.equal(iso(instanteDe(MADRID, 2026, 10, 25, 3, 30)), '2026-10-25T02:30');
  // Nueva York, 8 de marzo de 2026: de las 02:00 a las 03:00 (07:00 UTC).
  assert.equal(iso(instanteDe('America/New_York', 2026, 3, 8, 2, 15)), '2026-03-08T07:15');
});

test('cada día: hoy si aún no ha pasado, mañana si ya; estrictamente después', () => {
  const r = { hora: '09:00', frec: 'dia' };
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-10T07:59:59'))), '2026-11-10T08:00');
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-10T08:00:00'))), '2026-11-11T08:00');
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-10T22:00'))), '2026-11-11T08:00');
  // Cruza el cambio de hora: las 9:00 de reloj siguen siendo las 9:00.
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-03-28T08:00'))), '2026-03-29T07:00');
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-10-24T07:00'))), '2026-10-25T08:00');
  // De madrugada en Madrid aún es «ayer» en UTC: cuenta el día de la zona.
  assert.equal(iso(proximoAviso({ hora: '00:30', frec: 'dia' }, MADRID, utc('2026-11-10T22:50'))), '2026-11-10T23:30');
});

test('de lunes a viernes: se salta el fin de semana', () => {
  const r = { hora: '08:00', frec: 'laborables' };
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-13T06:00'))), '2026-11-13T07:00'); // Viernes por la mañana.
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-13T07:00'))), '2026-11-16T07:00'); // Ya pasó: el lunes.
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-14T12:00'))), '2026-11-16T07:00'); // Sábado.
  assert.equal(iso(proximoAviso(r, MADRID, utc('2026-11-15T12:00'))), '2026-11-16T07:00'); // Domingo.
});

test('cada semana: ese día (0 = domingo)', () => {
  assert.equal(iso(proximoAviso({ hora: '10:00', frec: 'semana', dia: 2 }, MADRID, utc('2026-11-10T08:00'))), '2026-11-10T09:00'); // Martes, antes de la hora.
  assert.equal(iso(proximoAviso({ hora: '10:00', frec: 'semana', dia: 2 }, MADRID, utc('2026-11-10T09:00'))), '2026-11-17T09:00');
  assert.equal(iso(proximoAviso({ hora: '10:00', frec: 'semana', dia: 0 }, MADRID, utc('2026-11-10T09:00'))), '2026-11-15T09:00');
  assert.equal(iso(proximoAviso({ hora: '10:00', frec: 'semana', dia: 1 }, MADRID, utc('2026-11-10T09:00'))), '2026-11-16T09:00');
});

test('cada mes: ese día, o el último si el mes no llega', () => {
  assert.equal(diasDelMes(2026, 2), 28);
  assert.equal(diasDelMes(2028, 2), 29);
  const r31 = { hora: '09:00', frec: 'mes', diaMes: 31 };
  assert.equal(iso(proximoAviso(r31, MADRID, utc('2026-01-31T08:00'))), '2026-02-28T08:00');
  assert.equal(iso(proximoAviso(r31, MADRID, utc('2026-02-28T08:00'))), '2026-03-31T07:00');
  assert.equal(iso(proximoAviso(r31, MADRID, utc('2026-03-31T07:00'))), '2026-04-30T07:00');
  assert.equal(iso(proximoAviso(r31, MADRID, utc('2028-02-01T00:00'))), '2028-02-29T08:00');
  const r10 = { hora: '21:15', frec: 'mes', diaMes: 10 };
  assert.equal(iso(proximoAviso(r10, MADRID, utc('2026-11-10T20:14'))), '2026-11-10T20:15');
  assert.equal(iso(proximoAviso(r10, MADRID, utc('2026-11-10T20:15'))), '2026-12-10T20:15');
  assert.equal(iso(proximoAviso(r10, MADRID, utc('2026-12-10T20:15'))), '2027-01-10T20:15');
});

test('cada mes, los días 29, 30 y 31: en los meses que no llegan, el último; y siempre hay un próximo', () => {
  const dia = (diaMes) => ({ hora: '09:00', frec: 'mes', diaMes });
  // Febrero de 2027 (28 días): los tres caen el 28.
  for (const diaMes of [29, 30, 31]) assert.equal(iso(proximoAviso(dia(diaMes), MADRID, utc('2027-02-01T00:00'))), '2027-02-28T08:00', String(diaMes));
  // Febrero de 2028 (bisiesto): el 29 es el 29, y el 30 y el 31 también.
  for (const diaMes of [29, 30, 31]) assert.equal(iso(proximoAviso(dia(diaMes), MADRID, utc('2028-02-01T00:00'))), '2028-02-29T08:00', String(diaMes));
  // Abril (30 días): el 31 cae el 30; el 30, en su día; el 29, también.
  assert.equal(iso(proximoAviso(dia(31), MADRID, utc('2027-04-01T00:00'))), '2027-04-30T07:00');
  assert.equal(iso(proximoAviso(dia(30), MADRID, utc('2027-04-01T00:00'))), '2027-04-30T07:00');
  assert.equal(iso(proximoAviso(dia(29), MADRID, utc('2027-04-01T00:00'))), '2027-04-29T07:00');
  // Tras el último día de febrero no se repite ese mes: el siguiente es en marzo, en su día de verdad.
  assert.equal(iso(proximoAviso(dia(30), MADRID, utc('2027-02-28T08:00'))), '2027-03-30T07:00');
  // Un año entero del día 31: doce avisos, uno por mes y cada uno en el último día que toca.
  let instante = utc('2026-12-31T23:00');
  const dias = [];
  for (let i = 0; i < 12; i += 1) {
    instante = proximoAviso(dia(31), MADRID, instante);
    dias.push(relojEn(MADRID, instante).dia);
  }
  assert.deepEqual(dias, [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]);
});

test('cambios de hora y frecuencias: la hora que no existe y la que existe dos veces, también al repetir', () => {
  // Cada día a las 02:30 en Madrid. El 29 de marzo de 2026 esa hora no existe: llega tras el salto (01:30 UTC)…
  const madrugada = { hora: '02:30', frec: 'dia' };
  assert.equal(iso(proximoAviso(madrugada, MADRID, utc('2026-03-28T01:30'))), '2026-03-29T01:30');
  // …y al día siguiente, a su hora de siempre (ya en horario de verano).
  assert.equal(iso(proximoAviso(madrugada, MADRID, utc('2026-03-29T01:30'))), '2026-03-30T00:30');
  // El 25 de octubre las 02:30 existen dos veces: avisa en la primera y no repite en la segunda.
  assert.equal(iso(proximoAviso(madrugada, MADRID, utc('2026-10-24T00:30'))), '2026-10-25T00:30');
  assert.equal(iso(proximoAviso(madrugada, MADRID, utc('2026-10-25T00:30'))), '2026-10-26T01:30');
  // De lunes a viernes y cada semana cruzan el cambio sin moverse de su hora de reloj.
  assert.equal(iso(proximoAviso({ hora: '08:00', frec: 'laborables' }, MADRID, utc('2026-03-27T07:00'))), '2026-03-30T06:00');
  assert.equal(iso(proximoAviso({ hora: '08:00', frec: 'semana', dia: 0 }, MADRID, utc('2026-10-18T06:00'))), '2026-10-25T07:00');
});

test('otras zonas: el día y la hora son los de quien puso el recordatorio', () => {
  const r = { hora: '07:00', frec: 'semana', dia: 1 }; // Los lunes a las 7:00.
  assert.equal(iso(proximoAviso(r, 'Pacific/Auckland', utc('2026-11-14T00:00'))), '2026-11-15T18:00'); // El lunes de allí aún es domingo en UTC.
  assert.equal(iso(proximoAviso(r, 'America/Los_Angeles', utc('2026-11-16T14:00'))), '2026-11-16T15:00');
  assert.equal(iso(proximoAviso({ hora: '09:00', frec: 'dia' }, 'Asia/Kolkata', utc('2026-11-10T03:30'))), '2026-11-11T03:30');
});

test('minutoDe: una clave que se ordena como el tiempo', () => {
  assert.equal(minutoDe(utc('2026-11-10T08:00:59')), '202611100800');
  assert.equal(minutoDe(utc('2027-01-02T03:04')), '202701020304');
  assert.ok(minutoDe(utc('2026-12-31T23:59')) < minutoDe(utc('2027-01-01T00:00')));
  assert.equal(diaDe(utc('2026-11-10T23:59:59')), '20261110');
  assert.equal(diaDe(utc('2026-11-11T00:00')), '20261111');
});
