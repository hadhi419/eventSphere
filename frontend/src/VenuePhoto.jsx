import React, { useState } from 'react'

function Photo({ src, name }) {
  const [failed, setFailed] = useState(false)
  return failed ? null : <img className="venue-photo" src={src} alt={name ? 'Venue: ' + name : 'Venue photo'}
    loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
}

export default function VenuePhoto({ src, name }) {
  return src ? <Photo key={src} src={src} name={name} /> : null
}
