{{- define "radiator.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "radiator.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "radiator.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end -}}

{{- define "radiator.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "radiator.secretName" -}}
{{ .Values.existingSecret | default (printf "%s-secrets" (include "radiator.fullname" .)) }}
{{- end -}}

{{- define "radiator.postgresName" -}}
{{ include "radiator.fullname" . }}-postgres
{{- end -}}

{{- define "radiator.postgresSecretName" -}}
{{ .Values.postgres.existingSecret | default (include "radiator.postgresName" .) }}
{{- end -}}

{{- define "radiator.secretEnv" -}}
- name: RADIATOR_DATABASE_URL
  valueFrom: {secretKeyRef: {name: {{ include "radiator.secretName" . }}, key: database-url}}
{{- end -}}
