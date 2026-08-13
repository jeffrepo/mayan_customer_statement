# Estado de cuenta de clientes - Mayan Golf

Repositorio para el addon Odoo 18 `mayan_customer_statement`. La raíz de este
repositorio es directamente la raíz del addon.

## Alcance

- Wizard en **Contabilidad > Clientes > Estado de cuenta Mayan**.
- Selección múltiple de clientes, año y mes.
- El periodo predeterminado es el mes anterior.
- Generación de un PDF con una página inicial por cliente.
- Resumen de saldo anterior, compras y cadis, otros cargos, subtotal de
  cargos, pagos y saldo al corte.
- Detalle por fecha, número de documento, descripción, débito y crédito.
- Uso de `fecha_estado_cuenta` para asignar facturas y notas al periodo.
- Número FEL con `fac_serie` y `fac_numero`; las notas internas conservan el
  número de documento de Odoo.

## Dependencias

- `account`
- `account_debit_note`
- `point_of_sale`
- `mayangolf`
- `l10n_gt_sat`

## Reglas de clasificación

### Compras y cadis

Se incluyen facturas POS enlazadas a una orden que tenga al menos un método de
pago con el booleano **Identificar Cliente** habilitado. El addon detecta el
campo por su nombre técnico conocido o por su etiqueta. Si el campo no existe,
usa como respaldo estos nombres normalizados:

- CREDITO RES
- CREDITO PIS
- CREDITO RAN
- CREDITO SERVICIOS

### Otros cargos

Se incluyen facturas/notas de cargo internas que contengan el producto o una
línea denominada `Recargo`.

### Pagos

Se incluyen pagos de cliente recibidos y notas de crédito de cliente del
periodo. El saldo anterior aplica las mismas reglas a todos los movimientos
anteriores al primer día del mes solicitado.

## Instalación

1. Clonar o copiar este repositorio directamente como
   `<ruta_addons>/mayan_customer_statement` en Odoo 18; no requiere una carpeta
   interna adicional.
2. Actualizar la lista de aplicaciones.
3. Instalar **Estado de cuenta de clientes - Mayan Golf**.
4. Verificar que los métodos POS de crédito tengan habilitado **Identificar
   Cliente** y que el producto de recargo se denomine `Recargo`.

Actualización por línea de comandos:

```bash
odoo-bin -d NOMBRE_BD -u mayan_customer_statement --stop-after-init
```

## Validación recomendada

Antes de producción, comparar un cliente y un mes contra el reporte histórico
de Mayan, verificando especialmente facturas con `fecha_estado_cuenta`, pagos
multimoneda, notas de crédito y órdenes POS con varios métodos de pago.
