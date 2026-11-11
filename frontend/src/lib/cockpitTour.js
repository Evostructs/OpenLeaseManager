import { cockpitBuildings, cockpitDetailAccess } from './cockpitDomain.js'

export const COCKPIT_TOUR_SECONDS = 12

export function cockpitTourStops(snapshot, user) {
  const stops = snapshot?.scope?.scope_type === 'portfolio'
    ? [{ buildingCode: '', topic: 'overview' }]
    : []
  for (const building of cockpitBuildings(snapshot).filter(row => row.selectable)) {
    const access = cockpitDetailAccess(user, building.code)
    stops.push({ buildingCode: building.code, topic: 'overview' })
    if (access.allowed) {
      stops.push({ buildingCode: building.code, topic: 'rent' })
      stops.push({ buildingCode: building.code, topic: 'operations' })
      if (access.tabs.includes('rooms')) stops.push({ buildingCode: building.code, topic: 'space' })
    }
  }
  // A single summary-only building still has distinct, permitted views to tour.
  if (stops.length === 1) {
    return ['occupancy.area_rate', 'rent.effective_daily', 'arrears.balance']
      .map(metric => ({ ...stops[0], metric }))
  }
  return stops
}

export function nextCockpitTourStop(stops, current) {
  if (!stops.length) return null
  const index = stops.findIndex(stop => stop.buildingCode === current.buildingCode
    && stop.topic === current.topic && (!stop.metric || stop.metric === current.metric))
  if (index >= 0) return stops[(index + 1) % stops.length]
  return stops.find(stop => stop.buildingCode === current.buildingCode) || stops[0]
}
