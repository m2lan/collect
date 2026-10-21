import http from './http'

export const login = (body) => http.post('/auth/login', body)
export const me = () => http.get('/auth/me')

export const listSources = () => http.get('/sources')
export const getSource = (id) => http.get(`/sources/${id}`)
export const createSource = (body) => http.post('/sources', body)
export const updateSource = (id, body) => http.put(`/sources/${id}`, body)
export const deleteSource = (id) => http.delete(`/sources/${id}`)
export const crawlSource = (id) => http.post(`/sources/${id}/crawl`)
export const clearSession = (id) => http.post(`/sources/${id}/logout`)
export const listRules = (id) => http.get(`/sources/${id}/rules`)
export const activateRule = (id, ruleId) => http.post(`/sources/${id}/rules/${ruleId}/activate`)
export const preview = (body) => http.post('/preview', body)

export const listLots = (params) => http.get('/lots', { params })
export const getLot = (id) => http.get(`/lots/${id}`)

export const listRuns = (params) => http.get('/runs', { params })

export const getAISettings = () => http.get('/ai/settings')
export const putAISettings = (body) => http.put('/ai/settings', body)
export const listProviders = () => http.get('/ai/providers')
export const createProvider = (body) => http.post('/ai/providers', body)
export const updateProvider = (id, body) => http.put(`/ai/providers/${id}`, body)
export const deleteProvider = (id) => http.delete(`/ai/providers/${id}`)
export const testProvider = (id) => http.post(`/ai/providers/${id}/test`)
export const aiGenerate = (body) => http.post('/ai/generate', body, { timeout: 600000 })
export const listGenerations = (params) => http.get('/ai/generations', { params })
export const aiUsage = () => http.get('/ai/usage')

export const snapshot = (body) => http.post('/snapshot', body)
export const getRun = (id) => http.get(`/runs/${id}`)
export const listSchedules = (id) => http.get(`/sources/${id}/schedules`)
export const saveSchedules = (id, body) => http.put(`/sources/${id}/schedules`, body)

export const getAlertSettings = () => http.get('/settings/alert')
export const putAlertSettings = (body) => http.put('/settings/alert', body)
export const testAlert = (body) => http.post('/settings/alert/test', body)

export async function exportLots(params) {
  const resp = await http.get('/lots/export', { params, responseType: 'blob', timeout: 600000 })
  return resp
}

export const sniff = (body) => http.post('/sniff', body)
export const crawlLive = (id) => http.post(`/sources/${id}/crawl`, null, { params: { kind: 'live' } })
export const listTemplates = () => http.get('/templates')
export const createTemplate = (body) => http.post('/templates', body)
export const deleteTemplate = (id) => http.delete(`/templates/${id}`)
export const getCrawlerSettings = () => http.get('/settings/crawler')
export const putCrawlerSettings = (body) => http.put('/settings/crawler', body)
