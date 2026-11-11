/* Cosas · recordatorios: la función programada. Cada cinco minutos mira a quién le toca un aviso de las cosas de su
   grupo Recordar (los guarda la app con /api/avisos/recordar, ver avisos.mjs) y se los envía a los dispositivos de
   esa sesión: varias cosas pueden viajar en un mismo mensaje push, y el service worker enseña un aviso por cosa. Cada
   una lleva la ficha con la que su botón «Hecho» la marca (y deja de avisarse de ella); «Mantener» no hace nada
   aquí: la cosa sigue con su recordatorio y se volverá a avisar cuando toque.

   Cada cinco minutos y no cada minuto porque en la app las horas se eligen de cinco en cinco, y así gasta la quinta
   parte del cupo gratuito de Netlify. Sin nada que configurar: Netlify la ejecuta sola (solo en el sitio publicado, en
   UTC) y todo lo suyo vive en Netlify Blobs. Puede ejecutarla dos veces, con retraso o cortarla a medias: un aviso
   puede repetirse, nunca perderse. Lo que toca se toma con un turno que caduca, se envía y solo se da por avisado lo
   que ha llegado; lo demás lo repite la ejecución siguiente, y lo atrasado sale en cuanto se puede (hasta seis horas
   después; más tarde ya no). Ver ../lib/recordatorios.mjs.

   Una vez al día (en la ejecución de las 03:00 UTC) borra además los documentos que ya no hacen falta: los de las
   sesiones que llevan un mes sin dispositivos a los que avisar y los que llevan un mes sin esperar ningún aviso (todo
   hecho, o ya sin cosas) y sin que su dueño los toque. */

import { almacen, dispositivosDe, enviarRecordatorios, llavesDe } from '../lib/envio.mjs';
import { limpiar, pasada, tocaLimpiar } from '../lib/recordatorios.mjs';

// Tiene que ser un literal escrito aquí: Netlify lo lee del código al publicar.
export const config = { schedule: '*/5 * * * *' };

// Una ejecución programada dispone de 30 segundos: lo que no dé tiempo se queda en la agenda para la siguiente.
const PLAZO = 18000;

export default async function () {
  const inicio = Date.now();
  const seguir = () => Date.now() - inicio < PLAZO;
  const store = almacen();
  // Lo primero, antes de tomar ningún aviso: si las claves no se pueden leer, se acaba aquí sin haber tocado nada.
  const llaves = await llavesDe(store);
  const cuenta = await pasada(store, inicio, {
    dispositivos: (uid) => dispositivosDe(store, uid),
    enviar: (uid, dispositivos, cosas) => enviarRecordatorios(store, llaves, uid, dispositivos, cosas, seguir),
    seguir,
  });
  let borrados = 0;
  if (tocaLimpiar(inicio)) {
    borrados = await limpiar(store, inicio, seguir).catch((error) => {
      console.error('Limpieza de recordatorios', error);
      return 0;
    });
  }
  const { sesiones, avisos, pausadas } = cuenta;
  console.log(`Recordatorios: ${sesiones} sesiones atendidas, ${avisos} cosas avisadas, ${pausadas} sesiones en pausa, ${borrados} documentos borrados`);
}
