{{- define "harness.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "harness.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "harness.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "harness.secretName" -}}
{{ .Values.existingSecret | default (printf "%s-secrets" (include "harness.fullname" .)) }}
{{- end -}}

{{/* Where the suites come from: before any `--`. */}}
{{- define "harness.testsArgs" -}}
{{- if .Values.tests.testsUrl }}
- --tests_url
- {{ .Values.tests.testsUrl | quote }}
{{- else if .Values.tests.download }}
- --download
{{- end }}
{{- end -}}

{{- define "harness.env" -}}
- name: PYTHONUNBUFFERED
  value: "1"
- name: RADIATOR_URL
  value: {{ required "radiator.url is required" .Values.radiator.url | quote }}
{{- with .Values.radiator.publicUrl }}
- name: RADIATOR_PUBLIC_URL
  value: {{ . | quote }}
{{- end }}
- name: RADIATOR_TOKEN
  valueFrom: {secretKeyRef: {name: {{ include "harness.secretName" . }}, key: radiator-token}}
{{- range $env, $key := dict "SLACK_WEBHOOK_URL" "slack-webhook-url" "SLACK_TOKEN" "slack-token" "SLACK_CHANNEL" "slack-channel" "ZE_BASE_URL" "ze-base-url" "ZE_REFRESH_TOKEN" "ze-refresh-token" }}
- name: {{ $env }}
  valueFrom: {secretKeyRef: {name: {{ include "harness.secretName" $ }}, key: {{ $key }}, optional: true}}
{{- end }}
{{- with .Values.queryParameters }}
- name: QUERY_PARAMETERS
  value: {{ . | quote }}
{{- end }}
{{- end -}}

{{/* The pod spec around one container; pass (dict "root" $ "container" ...). */}}
{{- define "harness.podSpec" -}}
{{- $ := .root -}}
restartPolicy: Never
{{- with $.Values.imagePullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $.Values.podSecurityContext }}
securityContext: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $.Values.nodeSelector }}
nodeSelector: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $.Values.tolerations }}
tolerations: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $.Values.affinity }}
affinity: {{- toYaml . | nindent 2 }}
{{- end }}
containers:
  {{- .container | nindent 2 }}
volumes:
  # results land here; what matters is uploaded to the radiator and Slack
  - {name: results, emptyDir: {}}
{{- end -}}
