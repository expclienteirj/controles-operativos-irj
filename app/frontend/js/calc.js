/* Cálculo de vista previa.
 *
 * Espejo reducido de app/backend/calc.py para que la tablet muestre el % al
 * instante y sin conexión. NO es la fuente de verdad: el porcentaje que se
 * certifica lo calcula siempre el backend. Si los dos difieren, manda el
 * backend — por eso la UI rotula estos valores como "preview".
 *
 * Mantener sincronizado con calc.py: VALOR_ESTADO y sector_limpieza(). */

const Calc = (() => {
  const VALOR_ESTADO = {
    CUMPLE: 1.0,
    DESVIO_PARCIAL: 0.5,
    DESVIO_TOTAL: 0.0,
  };
  const NO_VERIFICABLE = 'NO_VERIFICABLE';

  /**
   * % de un sector. Refleja la regla central: sin confirmar es "sin datos"
   * (null), no 100%.
   * @param {number[]} itemsIds  ítems activos del sector
   * @param {Object} desvios     { itemId: {estado} } solo los que tienen desvío
   * @param {boolean} confirmado
   */
  function sector(itemsIds, desvios, confirmado) {
    if (!confirmado) return null;

    const valores = [];
    for (const id of itemsIds) {
      const d = desvios[id];
      const estado = d ? d.estado : 'CUMPLE';
      if (estado === NO_VERIFICABLE) continue;   // excluido del cálculo
      valores.push(VALOR_ESTADO[estado]);
    }
    if (!valores.length) return null;
    return valores.reduce((a, b) => a + b, 0) / valores.length;
  }

  /** Promedio ignorando los null (sectores sin datos). */
  function promedio(valores) {
    const v = valores.filter((x) => x !== null && x !== undefined);
    if (!v.length) return null;
    return v.reduce((a, b) => a + b, 0) / v.length;
  }

  function porcentaje(valor, decimales = 1) {
    if (valor === null || valor === undefined) return '—';
    return (valor * 100).toFixed(decimales).replace('.', ',') + '%';
  }

  /** Todos los días de un período 'YYYY-MM', en ISO. Espejo de calc.dias_del_mes. */
  function diasDelMes(periodo) {
    const [anio, mes] = periodo.split('-').map(Number);
    const ultimo = new Date(anio, mes, 0).getDate();
    const dias = [];
    for (let d = 1; d <= ultimo; d++) {
      dias.push(`${periodo}-${String(d).padStart(2, '0')}`);
    }
    return dias;
  }

  return { VALOR_ESTADO, NO_VERIFICABLE, sector, promedio, porcentaje, diasDelMes };
})();
