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

Se incluyen todas las demás facturas de cliente publicadas en el periodo que
no fueron clasificadas como compras y cadis. Esto abarca cuotas, cargos
internos y consumos POS pagados con un método que no identifica al cliente.

### Pagos

Se incluyen pagos de cliente recibidos y notas de crédito de cliente del
periodo.

### Fórmulas del resumen

- **Saldo anterior:** todas las facturas del cliente menos notas de crédito y
  pagos anteriores al primer día del mes solicitado. Para las facturas se
  respeta `fecha_estado_cuenta` cuando está informada.
- **Subtotal cargos:** compras y cadis + otros cargos.
- **Saldo al corte:** saldo anterior + subtotal cargos - pagos.

## Instalación

1. Clonar o copiar este repositorio directamente como
   `<ruta_addons>/mayan_customer_statement` en Odoo 18; no requiere una carpeta
   interna adicional.
2. Actualizar la lista de aplicaciones.
3. Instalar **Estado de cuenta de clientes - Mayan Golf**.
4. Verificar que los métodos POS de crédito tengan habilitado **Identificar
   Cliente** para que sus facturas aparezcan en **Compras y cadis**. Las demás
   facturas aparecerán en **Otros cargos**.

Actualización por línea de comandos:

```bash
odoo-bin -d NOMBRE_BD -u mayan_customer_statement --stop-after-init
```

## Validación recomendada

Antes de producción, comparar un cliente y un mes contra el reporte histórico
de Mayan, verificando especialmente facturas con `fecha_estado_cuenta`, pagos
multimoneda, notas de crédito y órdenes POS con varios métodos de pago.

### Diagnóstico de continuidad mensual

El script `scripts/diagnose_statement_rollforward.py` compara, sin modificar
datos, el saldo al corte de un mes contra el saldo anterior del mes siguiente.
También lista:

- facturas y notas de crédito con su clasificación en el reporte;
- facturas que entran al saldo histórico, pero no a los cargos del mes;
- pagos incluidos y pagos excluidos por estado o importe;
- apuntes del mayor contable de cuentas por cobrar;
- diferencias contra importes de un reporte histórico, si se proporcionan.

Primero actualice el addon en la base de datos. Para diagnosticar al socio 1836
en junio de 2026 y compararlo con el reporte histórico entregado:

```bash
STATEMENT_COMPANY_ID=2 \
STATEMENT_PARTNER_CODE=1836 \
STATEMENT_YEAR=2026 \
STATEMENT_MONTH=6 \
STATEMENT_REFERENCE_OPENING=7811.10 \
STATEMENT_REFERENCE_CHARGES=10468.70 \
STATEMENT_REFERENCE_PAYMENTS=11261.40 \
STATEMENT_REFERENCE_CLOSING=7018.40 \
odoo-bin shell -d NOMBRE_BD \
  < scripts/diagnose_statement_rollforward.py
```

En la base de prueba de Mayan, la asociación utilizada por el reporte es la
compañía 2. Definirla expresamente evita que la shell tome otra compañía activa
con un nombre similar y devuelva movimientos en cero.

Esa referencia histórica incluye el recibo `PPCRCC/2026/00587` por Q 7,811.10.
El diagnóstico permitirá confirmar si existe en `account.payment`, si su estado
lo excluye del reporte nuevo o si únicamente aparece en el mayor de cuentas por
cobrar.

La compañía predeterminada es la compañía activa del entorno de Odoo. Se puede
indicar otra con `STATEMENT_COMPANY_ID`. También se puede seleccionar el cliente
directamente con `STATEMENT_PARTNER_ID`, que tiene prioridad sobre el código.
