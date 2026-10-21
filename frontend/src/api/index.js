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
