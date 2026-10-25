{{- define "urumi.name" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "urumi.labels" -}}
app.kubernetes.io/name: urumi
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "urumi.image" -}}
{{- if .digest -}}
{{ .repository }}@{{ .digest }}
{{- else -}}
{{ .repository }}:{{ .tag }}
{{- end -}}
{{- end -}}

{{- define "urumi.storageClass" -}}
{{- if eq .Values.persistence.storageClass "-" }}
storageClassName: ""
{{- else if .Values.persistence.storageClass }}
storageClassName: {{ .Values.persistence.storageClass | quote }}
{{- end }}
{{- end -}}

{{- define "urumi.storeUrl" -}}
{{ ternary "https" "http" .Values.ingress.tls.enabled }}://{{ .Values.ingress.storeHost }}
{{- end -}}

{{- define "urumi.wordpressEnv" -}}
- {name: WORDPRESS_DB_HOST, value: "{{ include "urumi.name" . }}-store-db:3306"}
- {name: WORDPRESS_DB_NAME, value: {{ .Values.databases.store.database | quote }}}
- {name: WORDPRESS_DB_USER, value: {{ .Values.databases.store.username | quote }}}
- name: WORDPRESS_DB_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.secrets.storeDatabase }}, key: password}}
- name: WORDPRESS_CONFIG_EXTRA
  valueFrom: {configMapKeyRef: {name: {{ include "urumi.name" . }}-wordpress, key: config-extra}}
{{- end -}}

{{- define "urumi.dashboardDatabaseEnv" -}}
- {name: MYSQL_HOST, value: "{{ include "urumi.name" . }}-dashboard-db"}
- {name: MYSQL_PORT, value: "3306"}
- {name: MYSQL_DATABASE, value: {{ .Values.databases.dashboard.database | quote }}}
- {name: MYSQL_USER, value: {{ .Values.databases.dashboard.username | quote }}}
- name: MYSQL_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.secrets.dashboardDatabase }}, key: password}}
{{- end -}}
