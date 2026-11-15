import VenuePhoto from './VenuePhoto.jsx'
import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'
import Workflow from './Workflow.jsx'

const date = value => new Date(value).toLocaleString('en-LK', {
  timeZone: 'Asia/Colombo', dateStyle: 'medium', timeStyle: 'short',
})
const money = value => new Intl.NumberFormat('en-LK', {
  style: 'currency', currency: 'LKR',
}).format(value)

async function api(path, { token, method = 'GET', data } = {}) {
  const headers = {}
  if (token) headers.Authorization = 'Bearer ' + token
  if (data) headers['Content-Type'] = 'application/json'
  const prefix = import.meta.env.DEV ? '/api' : ''
  const response = await fetch(prefix + path, {
    method, headers, body: data ? JSON.stringify(data) : undefined,
  })
  const value = await response.json()
  if (!response.ok) {
    throw Error(typeof value.detail === 'string' ? value.detail : 'Check the values you entered.')
  }
  if (['REFUSE', 'FAILURE'].includes(value.performative)) {
    throw Error(value.content.reason || 'The agent could not complete this request.')
  }
  return value
}

function TicketCard({ ticket, canBook, onBook, onJoin }) {
  return <article>
    <h2>{ticket.name}</h2>
    <p>{money(ticket.price_lkr)} / {ticket.remaining} available</p>
    {canBook && ticket.remaining > 0 && <form onSubmit={event => {
      event.preventDefault()
      onBook(new FormData(event.currentTarget).get('quantity'))
    }}>
      <label>Quantity<input name="quantity" type="number" min="1" required
        max={Math.min(6, ticket.remaining)} defaultValue="1" /></label>
      <button>Book tickets</button>
    </form>}
    {canBook && ticket.remaining < 1 && <button onClick={onJoin}>Join waitlist</button>}
  </article>
}

function App() {
  const [venues, setVenues] = useState([])
  const [account, setAccount] = useState(null)
  const [view, setView] = useState({ type: 'events', data: [] })
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const attendee = account?.role === 'attendee'
  const sections = {
    attendee: { waitlist: 'My waitlist', rentals: 'Private hire' },
    admin: { dashboard: 'Event dashboard', venues: 'Manage venues', reviews: 'Review hires', messages: 'Agent messages' },
    staff: { gate: 'Gate check-in' },
  }[account?.role] || {}

  const workflowTitles = {
    ...sections,
    ...(attendee ? { 'hire-new': 'Request private hire' } : {}),
    ...(account?.role === 'admin' ? {
      'venue-new': 'Create venue', 'venue-edit': 'Edit venue', 'event-new': 'Create event draft',
    } : {}),
  }

  function navigate(type, data = []) {
    setNotice('')
    setView({ type, data })
  }

  async function run(task) {
    setBusy(true)
    setNotice('')
    try { await task() }
    catch (error) { setNotice(error.message) }
    finally { setBusy(false) }
  }

  async function browse(form) {
    const params = new URLSearchParams(form)
    for (const [key, value] of Object.entries(Object.fromEntries(params))) {
      if (!value.trim()) params.delete(key)
    }
    const events = await api('/events?' + params)
    setView({ type: 'events', data: events.filter(event => Date.parse(event.starts_at) > Date.now()) })
  }

  async function privateView(type) {
    const options = { token: account.access_token }
    const data = await api('/' + type, options)
    const email = type === 'notifications' ? (await api('/notifications/email', options)).email : null
    setView({ type, data, email })
  }

  useEffect(() => {
    run(browse)
    api('/venues').then(setVenues).catch(() => setVenues([]))
  }, [])

  const { type, data } = view
  const title = { events: 'Upcoming events', bookings: 'My bookings',
    notifications: 'Notifications', detail: data.title, ...workflowTitles }[type]

  if (type === 'login') return <main className="auth-screen">
    <button className="secondary-button" disabled={busy} onClick={() => run(browse)}>Back to events</button>
    <p className="notice" role="status">{busy ? 'Signing in...' : notice}</p>
    <fieldset disabled={busy}>
        <article className="auth-card"><h1>Sign in</h1><form onSubmit={event => {
          event.preventDefault()
          const values = Object.fromEntries(new FormData(event.currentTarget))
          const register = event.nativeEvent.submitter?.value === 'register'
          run(async () => {
            if (register) await api('/auth/register', { method: 'POST', data: values })
            setAccount(await api('/auth/login', { method: 'POST', data: values }))
            await browse()
          })
        }}>
          <label>Username<input name="username" autoComplete="username" required /></label>
          <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
          <button>Sign in</button><button value="register">Register attendee</button>
        </form></article>
    </fieldset>
  </main>

  return <div className="app-shell">
    <header><nav aria-label="Main navigation">
      <strong className="brand"><span className="brand-mark" aria-hidden="true">E</span>EventSphere</strong>
      <fieldset disabled={busy}>
        <button aria-current={type === "events" || type === "detail" ? "page" : undefined} onClick={() => run(browse)}>Events</button>
        {attendee && <>
          <button aria-current={type === "bookings" ? "page" : undefined} onClick={() => run(() => privateView('bookings'))}>My bookings</button>
          <button aria-current={type === "notifications" ? "page" : undefined} onClick={() => run(() => privateView('notifications'))}>Notifications</button>
        </>}
        {Object.entries(sections).map(([key, label]) => <button key={key} aria-current={type === key ? "page" : undefined}
          onClick={() => navigate(key)}>{label}</button>)}
        <button className="account-button" onClick={() => {
          if (!account) return navigate('login')
          setAccount(null)
          setView({ type: 'events', data: [] })
          run(browse)
        }}>{account ? 'Sign out' : 'Sign in'}</button>
      </fieldset>
    </nav></header>
    <main>
      <div className={type === 'events' ? 'page-heading discovery' : 'page-heading'}>
        {account && <span className="role-label">{account.role === 'staff' ? 'Gate staff' : account.role}</span>}
        <h1>{title}</h1>
      </div>
      <p className="notice" role="status">{busy ? 'Loading...' : notice}</p>
      <fieldset disabled={busy}>
        {workflowTitles[type] && <Workflow key={type} mode={type} selected={data} navigate={navigate} run={run} report={setNotice}
          date={date} money={money}
          request={(path, options) => api(path, { ...options, token: account.access_token })} />}
        {type === 'events' && <>
          <form className="search" onSubmit={event => {
            event.preventDefault()
            const form = new FormData(event.currentTarget)
            run(() => browse(form))
          }}>
            <label>Search events<input name="keyword" type="search" /></label>
            <label>Category<input name="category" /></label>
            <details className="search-filters">
              <summary>Advanced filters</summary>
              <div className="search-filter-fields">
                <label>Venue<select name="venue_id"><option value="">All venues</option>
                  {venues.map(venue => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
                </select></label>
                <label>From date<input name="from_date" type="date" /></label>
                <label>To date<input name="to_date" type="date" /></label>
                <label>Minimum ticket price (LKR)<input name="min_price_lkr" type="number" min="0" step=".01" /></label>
                <label>Maximum ticket price (LKR)<input name="max_price_lkr" type="number" min="0" step=".01" /></label>
              </div>
            </details>
            <button>Search</button>
          </form>
          {!busy && !notice && !data.length && <p>No upcoming events match your search.</p>}
          <div className="grid event-grid">{data.map(event => <article className="event-card" key={event.id}>
            <VenuePhoto src={event.venue_image} name={event.venue_name} />
            <small>{event.category}</small><h2>{event.title}</h2>
            <p>{event.venue_name}<br />{date(event.starts_at)}</p>
            <button onClick={() => run(async () => {
              setView({ type: 'detail', data: await api('/events/' + encodeURIComponent(event.id)) })
            })}>View tickets</button>
          </article>)}</div>
        </>}
        {type === 'detail' && <>
          <p>{date(data.starts_at)} to {date(data.ends_at)} (Sri Lanka time)</p>
          <VenuePhoto src={data.venue_image} name={data.venue_name} />
          {data.venue_name && <p>{data.venue_name}</p>}
          <p>{data.description}</p>
          <div className="grid">{data.tickets.map(ticket => <TicketCard key={ticket.id}
            ticket={ticket} canBook={attendee} onJoin={() => run(async () => {
              const params = new URLSearchParams({ category_id: ticket.id })
              await api('/events/' + encodeURIComponent(data.id) + '/waitlist?' + params, {
                method: 'POST', token: account.access_token,
              })
              setNotice('Waitlist request accepted. See My waitlist for your status.')
            })} onBook={quantity => run(async () => {
              const params = new URLSearchParams({ category_id: ticket.id, quantity })
              const booking = await api('/events/' + encodeURIComponent(data.id) + '/book?' + params, {
                method: 'POST', token: account.access_token,
              })
              setNotice('Booking confirmed. Reference ' + booking.content.payment_reference)
              try { await privateView('bookings') }
              catch { setNotice('Booking confirmed. Open My bookings to see your tickets.') }
            })} />)}</div>
        </>}
        {type === 'bookings' && <>
          {!data.length && <p>You have no bookings yet.</p>}
          {data.map(booking => <article key={booking.payment_reference}>
            <h2>{booking.title}</h2>
            <p>{booking.quantity} ticket(s) / {money(booking.total_lkr)}</p>
            <small>{booking.payment_reference}</small>
            {booking.refund_lkr !== null ? <p>Cancelled. Refund {money(booking.refund_lkr)}</p> : <>
              {booking.codes.map((code, index) => <figure className="ticket-qr" key={code}>
                {booking.qr_codes?.[index] && <img src={booking.qr_codes[index]} width="160" height="160"
                  alt={'Check-in QR code for ticket ' + (index + 1)} />}
                <figcaption><code>{code}</code></figcaption>
              </figure>)}
              <button onClick={() => {
                if (!confirm('Cancel all tickets in this booking?')) return
                run(async () => {
                  const reply = await api('/bookings/' + encodeURIComponent(booking.payment_reference) + '/cancel', {
                    method: 'POST', token: account.access_token,
                  })
                  setView({ type: 'bookings', data: data.map(item =>
                    item.payment_reference === booking.payment_reference
                      ? { ...item, refund_lkr: reply.content.refund_lkr } : item) })
                  setNotice('Booking cancelled. Refund ' + money(reply.content.refund_lkr))
                })
              }}>Cancel booking</button>
            </>}
          </article>)}
        </>}
        {type === 'notifications' && <>
          <article><h2>Email notifications</h2>
            <form onSubmit={event => {
              event.preventDefault()
              const email = new FormData(event.currentTarget).get('email').trim() || null
              run(async () => {
                await api('/notifications/email', { method: 'PUT', token: account.access_token, data: { email } })
                setNotice(email ? 'Notification email saved.' : 'Email notifications turned off.')
              })
            }}>
              <label>Email address<input name="email" type="email" maxLength="254" defaultValue={view.email ?? ''} /></label>
              <button>Save email preference</button>
            </form>
            <p>Leave the email blank to turn off email notifications.</p>
          </article>
          {!data.length && <p>You have no notifications yet.</p>}
          {data.map(entry => <article key={entry.id}>{entry.body}</article>)}
        </>}
      </fieldset>
    </main>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
