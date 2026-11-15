import VenuePhoto from './VenuePhoto.jsx'
import React, { useEffect, useState } from 'react'
import TicketScanner from './TicketScanner.jsx'

const fields = form => Object.fromEntries(new FormData(form))
const post = data => ({ method: 'POST', data })
const id = encodeURIComponent
const localTime = (values, name) => values[name + '_date'] + 'T' + values[name + '_time']

function VenueSelect({ venues }) {
  return <label>Venue<select name="venue_id" required>
    <option value="">Choose a venue</option>
    {venues.map(venue => <option key={venue.id} value={venue.id}>{venue.name} ({venue.capacity} seats)</option>)}
  </select></label>
}

function Times() {
  return <>
    <div className="schedule-fields">
      <strong>Schedule ? Sri Lanka time (UTC+05:30)</strong>
      <div className="search-filter-fields">
        <label>Start date<input name="start_date" type="date" required /></label>
        <label>Start time<input name="start_time" type="time" required /></label>
        <label>End date<input name="end_date" type="date" required /></label>
        <label>End time<input name="end_time" type="time" required /></label>
      </div>
    </div>
  </>
}

function VenueEditor({ venue, request, run, navigate }) {
  return <article><h2>{venue ? venue.name : 'Add venue'}</h2>
    <form onSubmit={event => {
      event.preventDefault()
      const form = event.currentTarget
      const data = fields(form)
      run(async () => {
        await request(venue ? '/venues/' + id(venue.id) : '/venues', {
          method: venue ? 'PUT' : 'POST',
          data: { ...data, image: data.image || null, capacity: Number(data.capacity),
            hourly_rate_lkr: Number(data.rate), private_hire: data.private_hire === 'on' },
        })
        navigate('venues')
      })
    }}>
      <label>Name<input name="name" required maxLength="150" defaultValue={venue?.name} /></label>
      <label>Location<input name="location" required defaultValue={venue?.location} /></label>
      <label>Description<input name="description" defaultValue={venue?.description} /></label>
      <label>Image URL<input name="image" type="url" defaultValue={venue?.image} /></label>
      <label>Capacity<input name="capacity" type="number" min="1" max="100000" required defaultValue={venue?.capacity} /></label>
      <label>Hourly rate (LKR)<input name="rate" type="number" min="0" step=".01" required defaultValue={venue?.hourly_rate_lkr ?? 0} /></label>
      <label><input name="private_hire" type="checkbox" defaultChecked={venue?.private_hire} />Allow private hire</label>
      <button>Save venue</button>
    </form>
  </article>
}

function DraftForm({ venues, request, run, navigate }) {
  const [count, setCount] = useState(1)
  return <article><h2>Create an event draft</h2><form onSubmit={event => {
    event.preventDefault()
    const form = event.currentTarget
    const values = fields(form)
    const tickets = Array.from({ length: count }, (_, index) => ({
      name: values['name' + index], price_lkr: Number(values['price' + index]),
      quantity: Number(values['quantity' + index]),
    }))
    run(async () => {
      await request('/events/drafts', post({
        title: values.title, category: values.category, venue_id: values.venue_id,
        starts_at: localTime(values, 'start'), ends_at: localTime(values, 'end'),
        description: values.description, tickets,
      }))
      navigate('dashboard')
    })
  }}>
    <label>Title<input name="title" required /></label><label>Category<input name="category" required /></label>
    <VenueSelect venues={venues} /><Times /><label>Description<input name="description" /></label>
    {Array.from({ length: count }, (_, index) => <div className="ticket-fields" key={index}>
      <label>Ticket category<input name={'name' + index} maxLength="100" required /></label>
      <label>Price (LKR)<input name={'price' + index} type="number" min="0" step=".01" defaultValue="0" required /></label>
      <label>Quantity<input name={'quantity' + index} type="number" min="1" max="100000" required /></label>
    </div>)}
    <button type="button" disabled={count === 30} onClick={() => setCount(count + 1)}>Add ticket category</button>
    {count > 1 && <button type="button" onClick={() => setCount(count - 1)}>Remove last category</button>}
    <button>Save draft</button>
  </form></article>
}

function Offers({ result, date, money }) {
  return result?.alternatives?.length ? <article><h2>Alternative slots</h2><p>{result.reason}</p>
    <ul>{result.alternatives.map(slot => <li key={slot.venue_id + slot.starts_at}>
      <VenuePhoto src={slot.venue_image} name={slot.venue_name} />
      {slot.venue_name}, {date(slot.starts_at)} to {date(slot.ends_at)} / {money(slot.price_lkr)}
    </li>)}</ul>
  </article> : null
}

function CountChart({ title, rows, empty }) {
  const max = Math.max(1, ...rows.map(row => row.count))
  return <article className="dashboard-chart">
    <h2>{title}</h2>
    {rows.length ? <ul>{rows.map(row => <li key={row.key}>
      <span>{row.label}</span>
      <span className="chart-track" aria-hidden="true">
        <span style={{ width: (100 * row.count / max) + '%' }} />
      </span>
      <strong>{row.count}</strong>
    </li>)}</ul> : <p>{empty}</p>}
  </article>
}

const chartDate = new Intl.DateTimeFormat('en-LK', {
  timeZone: 'Asia/Colombo', month: 'short', day: 'numeric',
})

export default function Workflow({ mode, selected, navigate, request, run, report, date, money }) {
  const [checkIns, setCheckIns] = useState([])
  const [data, setData] = useState([])
  const [venues, setVenues] = useState([])
  const [offers, setOffers] = useState(null)
  const [now, setNow] = useState(Date.now())
  const [decision, setDecision] = useState('')
  const [gateEvent, setGateEvent] = useState('')
  const endpoints = { rentals: '/hires', waitlist: '/waitlist', dashboard: '/admin/dashboard',
    reviews: '/admin/hires', messages: '/admin/messages', gate: '/events' }

  async function refresh() {
    if (['venues', 'rentals', 'hire-new', 'event-new', 'reviews'].includes(mode)) setVenues(await request('/venues'))
    if (endpoints[mode]) setData(await request(endpoints[mode]))
    if (mode === 'dashboard') setCheckIns(await request('/admin/check-ins'))
  }

  useEffect(() => { run(refresh) }, [])
  useEffect(() => {
    if (mode !== 'waitlist') return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [mode])

  if (mode === 'venue-new' || mode === 'venue-edit') return <>
    <button className="secondary-button" onClick={() => navigate('venues')}>Back to venues</button>
    <VenueEditor venue={mode === 'venue-edit' ? selected : undefined} {...{ request, run, navigate }} />
  </>

  if (mode === 'venues') return <>
    <div className="screen-actions"><button onClick={() => navigate('venue-new')}>Create venue</button></div>
    {!venues.length && <p>No venues yet.</p>}
    <div className="grid">{venues.map(venue => <article key={venue.id}>
      <VenuePhoto src={venue.image} name={venue.name} />
      <h2>{venue.name}</h2><p>{venue.location} / {venue.capacity} seats</p>
      {venue.description && <p>{venue.description}</p>}
      <p>{venue.private_hire ? 'Private hire available ? ' + money(venue.hourly_rate_lkr) + ' per hour' : 'Private hire unavailable'}</p>
      <button onClick={() => navigate('venue-edit', venue)}>Edit venue</button>
    </article>)}</div>
  </>

  if (mode === 'event-new') return <>
    <button className="secondary-button" onClick={() => navigate('dashboard')}>Back to event dashboard</button>
    {venues.length ? <DraftForm {...{ venues, request, run, navigate }} /> :
      <article><p>Create a venue before adding an event draft.</p>
        <button onClick={() => navigate('venue-new')}>Create venue</button></article>}
  </>

  if (mode === 'dashboard') return <>
    <div className="screen-actions"><button onClick={() => navigate('event-new')}>Create event draft</button></div>
    <div className="dashboard-charts">
      <CountChart title="Tickets sold per event" empty="No ticket sales yet."
        rows={data.filter(event => event.tickets_sold > 0).map(event => ({
          key: event.id, label: event.title, count: Number(event.tickets_sold),
        }))} />
      <CountChart title="Check-ins by day ? Last 30 days" empty="No new check-ins recorded."
        rows={checkIns.map(day => ({
          key: day.day_start, label: chartDate.format(day.day_start * 1000), count: Number(day.count),
        }))} />
    </div>
    {!data.length && <p>No scheduled events yet.</p>}
    {data.map(event => <article key={event.id}>
      <VenuePhoto src={event.venue_image} name={event.venue_name} />
      <h2>{event.title}</h2><p>{event.venue_name} / {event.status}</p>
      <p>Sold {event.tickets_sold} of {event.ticket_capacity} / {event.remaining} available / {event.check_ins} checked in</p>
      <p>Venue capacity {event.venue_capacity} / Revenue {money(event.revenue_lkr)} / Refunds {money(event.refunds_lkr)}</p>
      {event.status !== 'cancelled' && <button onClick={() => {
        const publish = event.status === 'draft'
        if (!publish && !confirm('Cancel this event and refund active bookings?')) return
        run(async () => {
          let reply
          try {
            reply = await request('/events/' + id(event.id) + (publish ? '/publish' : '/cancel'), post())
          } catch (error) {
            if (!publish || !confirm(error.message + '\n\nDelete this draft? Choose Cancel to keep it.')) throw error
            await request('/events/drafts/' + id(event.id), { method: 'DELETE' })
            report('Draft deleted.')
            await refresh()
            return
          }
          report(publish ? 'Event published.' : 'Event cancelled. Refunded bookings: ' + reply.content.refunded_bookings)
          await refresh()
        })
      }}>{event.status === 'draft' ? 'Publish event' : 'Cancel event'}</button>}
    </article>)}
  </>

  if (mode === 'hire-new') return <>
    <button className="secondary-button" onClick={() => navigate('rentals')}>Back to my hire requests</button>
    <article><h2>Request private hire</h2><p>Saturday and Sunday hours cost 20% more.</p><form onSubmit={event => {
      event.preventDefault()
      const values = fields(event.currentTarget)
      setOffers(null)
      run(async () => {
        const reply = await request('/hires', post({
          ...values, guests: Number(values.guests),
          starts_at: localTime(values, 'start'), ends_at: localTime(values, 'end'),
        }))
        if (reply.performative === 'PROPOSE') return setOffers(reply.content)
        navigate('rentals', reply.content)
      })
    }}>
      <VenueSelect venues={venues.filter(venue => venue.private_hire)} /><Times />
      <label>Purpose<input name="purpose" required maxLength="500" /></label>
      <label>Guests<input name="guests" type="number" min="1" max="100000" required /></label>
      <button>Request hire</button>
    </form></article>
    <Offers result={offers} {...{ date, money }} />
  </>

  if (mode === 'rentals') return <>
    <div className="screen-actions"><button onClick={() => navigate('hire-new')}>Request private hire</button></div>
    {selected.status && <p role="status">Hire {selected.status}. Quote {money(selected.price_lkr)}</p>}
    {!data.length && <p>You have no hire requests yet.</p>}
    {data.map(hire => <article key={hire.id}>
      <VenuePhoto src={venues.find(venue => venue.id === hire.venue_id)?.image} name={venues.find(venue => venue.id === hire.venue_id)?.name} />
      <h2>{hire.purpose}</h2><p>{venues.find(venue => venue.id === hire.venue_id)?.name}</p>
      <p>{date(hire.starts_at)} to {date(hire.ends_at)} / {hire.status} / {money(hire.price_lkr)}</p>
    </article>)}
  </>

  if (mode === 'reviews') return <>
    <Offers result={offers} {...{ date, money }} />
    {!data.length && <p>No pending hire requests.</p>}
    {data.map(hire => <article key={hire.id}>
      <VenuePhoto src={venues.find(venue => venue.id === hire.venue_id)?.image} name={venues.find(venue => venue.id === hire.venue_id)?.name} />
      <h2>{hire.purpose}</h2><p>{hire.username} / {venues.find(venue => venue.id === hire.venue_id)?.name} / {hire.guests} guests</p>
      <p>{date(hire.starts_at)} to {date(hire.ends_at)} / {money(hire.price_lkr)}</p>
      <div className="actions">{[true, false].map(approve => <button key={String(approve)} onClick={() => {
        setOffers(null)
        run(async () => {
          const reply = await request('/admin/hires/' + id(hire.id) + '/review?approve=' + approve, post())
          if (reply.performative === 'PROPOSE') return setOffers(reply.content)
          report('Hire ' + reply.content.status + '.')
          await refresh()
        })
      }}>{approve ? 'Approve' : 'Reject'}</button>)}</div>
    </article>)}
  </>

  if (mode === 'waitlist') return <>
    <button onClick={() => run(refresh)}>Refresh offers</button>
    {!data.length && <p>You have no waitlist entries.</p>}
    {data.map(entry => {
      const seconds = Math.max(0, Math.ceil(entry.expires - now / 1000))
      return <article key={entry.id}>
        <h2>{entry.title || 'Ticket waitlist'}</h2>
        <p>{entry.category_name} / Status: {entry.status}{entry.status === 'held' && ' / ' + seconds + ' seconds left'}</p>
        {entry.status === 'held' && seconds > 0 && <button onClick={() => run(async () => {
          const reply = await request('/events/' + id(entry.event_id) + '/waitlist/' + id(entry.id) + '/claim', post())
          report('Offer claimed. Reference ' + reply.content.payment_reference + '. See My bookings for your ticket.')
          await refresh()
        })}>Claim ticket</button>}
      </article>
    })}
  </>

  async function checkIn(code) {
    setDecision('')
    return run(async () => {
      const reply = await request('/events/' + id(gateEvent) + '/scan', post({ code }))
      setDecision(reply.content.decision)
    })
  }

  if (mode === 'gate') return <article><h2>Check in a ticket</h2>
    <form onSubmit={event => {
      event.preventDefault()
      const form = event.currentTarget
      const values = fields(form)
      checkIn(values.code.trim())
      form.elements.code.value = ''
    }}>
      <label>Published event<select name="event_id" required value={gateEvent} onChange={event => { setGateEvent(event.target.value); setDecision('') }}>
        <option value="">Choose an event</option>
        {data.map(event => <option key={event.id} value={event.id}>{event.title} / {date(event.starts_at)}</option>)}
      </select></label>
      <label>Ticket code<input name="code" required autoComplete="off" /></label>
      <button>Check in</button>
    </form>
    {gateEvent && <TicketScanner key={gateEvent} onScan={checkIn} />}
    <p className="gate-decision" role="status">{decision}</p>
  </article>

  const conversations = new Map()
  for (const message of data) {
    if (!conversations.has(message.conversation_id)) conversations.set(message.conversation_id, [])
    conversations.get(message.conversation_id).push(message)
  }

  return <>
    <form onSubmit={event => {
      event.preventDefault()
      const values = fields(event.currentTarget)
      const params = new URLSearchParams({ limit: values.limit })
      if (values.conversation_id.trim()) params.set('conversation_id', values.conversation_id.trim())
      run(async () => setData(await request('/admin/messages?' + params)))
    }}>
      <label>Request ID<input name="conversation_id" /></label>
      <label>Request limit<select name="limit" defaultValue="25">
        <option value="10">10</option><option value="25">25</option><option value="50">50</option>
      </select></label>
      <button>Search messages</button>
    </form>
    {data.length ? <p>Showing {conversations.size} request traces with {data.length} agent messages.</p> :
      <p>No agent messages found.</p>}
    {Array.from(conversations, ([conversationId, messages], index) => {
      const first = messages.find(message => message.sender === 'API' && message.performative === 'REQUEST') ?? messages[0]
      const outcome = [...messages].reverse().find(message =>
        message.sender === first.receiver && message.receiver === first.sender && message.performative !== 'REQUEST')
      const action = first.content?.action ?? {
        BookingAgent: 'Book tickets', VenueAgent: 'Publish event',
      }[first.receiver] ?? 'Agent request'
      const result = outcome?.performative ?? 'No reply recorded'
      return <article className="message-group" key={conversationId}>
        <details open={index === 0}>
          <summary>
            <span className="trace-title">{action.replaceAll('_', ' ')}</span>
            <span className="trace-badge" data-result={result}>{result}</span>
            <span className="trace-meta">{first.receiver} ? {messages.length} messages</span>
          </summary>
          <p className="trace-meta">Request ID <code>{conversationId}</code></p>
          <ol className="trace-steps">{messages.map((message, step) => {
            const entries = Object.entries(message.content ?? {})
            return <li key={step}>
              <div className="trace-step-heading">
                <strong>{message.sender} &rarr; {message.receiver}</strong>
                <span className="trace-badge" data-result={message.performative}>{message.performative}</span>
              </div>
              {entries.length > 0 && <dl className="trace-payload">{entries.map(([field, value]) =>
                <div key={field}>
                  <dt>{field.replaceAll('_', ' ')}</dt>
                  <dd>{value !== null && typeof value === 'object'
                    ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value)}</dd>
                </div>
              )}</dl>}
            </li>
          })}</ol>
        </details>
      </article>
    })}
  </>
}
