# template-fix.md — Bug de interpolación de labels en `compose.traefik.yml`

## Contexto

Detectado analizando logs reales de producción de `docker-host`
(UrraHosting-Dashboard) al desplegar la plantilla hermana
(UrraHosting-GamePanel) como `runtime_mode=platform_stack` — este repo
comparte exactamente el mismo patrón en su propio `compose.traefik.yml`,
por lo que tiene el mismo bug aunque todavía no se haya visto reproducido
en un log de WebPanel específicamente. Log real (`urra-traefik`, del
deploy de GamePanel):

```
ERR error="the service \"panel-a5f9af60-341e-4119-aa37-f756a0ad927b@docker\" does not exist"
    entryPointName=websecure routerName=panel-${INSTANCE_ID}@docker
```

`routerName=panel-${INSTANCE_ID}@docker` — literal, sin interpolar, en vez
del UUID real de la instancia (que sí aparece correcto en el nombre del
*servicio* que el router busca, `panel-a5f9af60-...`).

## Causa raíz

`compose.traefik.yml` declaraba `labels:` como un **mapa YAML**, con
`${INSTANCE_ID}` incrustado dentro de las *keys* de los labels:

```yaml
labels:
  traefik.http.routers.panel-${INSTANCE_ID}.rule: "Host(`panel-${INSTANCE_ID}.${PUBLIC_BASE_DOMAIN}`)"
  traefik.http.services.panel-${INSTANCE_ID}.loadbalancer.server.port: "${DASHBOARD_PORT}"
```

**Docker Compose interpola `${VAR}` dentro de valores string, pero no
dentro de las *keys* de un mapa.** La key del router
(`panel-${INSTANCE_ID}`) queda literal, pero el *valor* del label
`...service: panel-${INSTANCE_ID}` sí se interpola bien (ahí
`${INSTANCE_ID}` está del lado del valor) y apunta correctamente a
`panel-<instance-id-real>`. El problema es que el **servicio**
correspondiente se registra con `traefik.http.services.panel-
${INSTANCE_ID}...` — de nuevo `${INSTANCE_ID}` en una key — así que el
servicio queda registrado bajo el nombre literal `panel-${INSTANCE_ID}` en
vez de `panel-<instance-id-real>`. Resultado: el router busca un servicio
que nunca existe con ese nombre → `service ... does not exist` → el panel
nunca responde aunque el contenedor `dashboard` esté sano.

Con una sola instancia de WebPanel corriendo, el único síntoma visible es
justo ese: el panel (`https://panel-<id>.<dominio>`) no conecta. Con **dos
o más instancias corriendo a la vez**, además ambas presentarían
exactamente el mismo label-key literal
(`traefik.http.routers.panel-${INSTANCE_ID}.rule`, sin diferenciar por
instancia) y Traefik solo se quedaría con un router, pisando al de la otra
instancia.

## Fix aplicado

`compose.traefik.yml`: `labels:` pasó de mapa a **lista de strings
`"key=value"`** en el servicio `dashboard`. Compose sí interpola de forma
confiable un string completo — al escribir la key y el valor juntos como
un solo string, `${INSTANCE_ID}` se expande en toda la línea, key incluida:

```yaml
labels:
  - "traefik.http.routers.panel-${INSTANCE_ID}.rule=Host(`panel-${INSTANCE_ID}.${PUBLIC_BASE_DOMAIN}`)"
  - "traefik.http.services.panel-${INSTANCE_ID}.loadbalancer.server.port=${DASHBOARD_PORT}"
```

Verificado con una simulación de la interpolación de Compose (sustitución
de `${VAR}` en cada string) contra el archivo ya corregido: con
`INSTANCE_ID=a5f9af60-341e-4119-aa37-f756a0ad927b`, tanto el router
`panel-a5f9af60-...` como el servicio `panel-a5f9af60-...` quedan con
**exactamente el mismo nombre** — ya no hay mismatch.

No se tocó nada más:
- `compose.yml` no usa `${INSTANCE_ID}` (ni ninguna otra variable
  por-instancia) dentro de una key de `labels:` — solo como *valor*
  (`com.urrahosting.instance: ${INSTANCE_ID}`, key fija), que interpola
  sin problema.
- Los labels que arma `orchestrator/app.py::activate()` para el contenedor
  `app` (y `database`) del tenant **no** usan este mecanismo: se
  construyen con f-strings de Python (`f"traefik.http.routers.app-
  {CONFIG.instance_id}.rule"`) y se pasan directo al SDK de Docker, con el
  valor real ya sustituido antes de llegar a Docker — no hay interpolación
  de Compose de por medio, así que no tienen este bug.
