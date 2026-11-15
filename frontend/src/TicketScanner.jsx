import React, { useEffect, useRef, useState } from 'react'

export default function TicketScanner({ onScan }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const video = useRef(null)
  const callback = useRef(onScan)
  callback.current = onScan

  useEffect(() => {
    if (!open) return
    let stopped = false
    let stream
    let timer
    function stop() {
      stopped = true
      clearTimeout(timer)
      stream?.getTracks().forEach(track => track.stop())
    }

    async function start() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw Error('Camera access requires HTTPS or localhost.')
        const Detector = window.BarcodeDetector
        let detect
        if (Detector && (await Detector.getSupportedFormats()).includes('qr_code')) {
          const detector = new Detector({ formats: ['qr_code'] })
          detect = async camera => (await detector.detect(camera))[0]?.rawValue
        } else {
          const { default: decodeQR } = await import('jsqr')
          const canvas = document.createElement('canvas')
          const context = canvas.getContext('2d', { willReadFrequently: true })
          detect = camera => {
            const scale = Math.min(1, 960 / Math.max(camera.videoWidth, camera.videoHeight))
            const width = Math.max(1, Math.round(camera.videoWidth * scale))
            const height = Math.max(1, Math.round(camera.videoHeight * scale))
            if (canvas.width !== width) canvas.width = width
            if (canvas.height !== height) canvas.height = height
            context.drawImage(camera, 0, 0, width, height)
            const frame = context.getImageData(0, 0, width, height)
            return decodeQR(frame.data, width, height)?.data
          }
        }
        if (stopped) return
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
        if (stopped) return stop()
        const camera = video.current
        camera.srcObject = stream
        await camera.play()
        while (!stopped) {
          const code = camera.readyState >= 2 && camera.videoWidth
            ? (await detect(camera))?.trim() : null
          if (stopped) return
          if (code) {
            stop()
            setOpen(false)
            callback.current(code)
            return
          }
          await new Promise(resolve => { timer = setTimeout(resolve, 150) })
        }
      } catch (failure) {
        if (stopped) return
        stop()
        setOpen(false)
        const cameraErrors = {
          NotAllowedError: 'Camera access was denied. Allow camera access for EventSphere in Chrome and Windows settings, then retry.',
          NotFoundError: 'No camera was found. Check that your webcam is connected and enabled.',
          NotReadableError: 'Chrome could not start the webcam. Close other apps using it and retry.',
        }
        setError(cameraErrors[failure.name] ?? failure.message)
      }
    }
    start()
    return stop
  }, [open])

  return <div className="scanner">
    <button type="button" onClick={() => { setError(''); setOpen(!open) }}>
      {open ? 'Close camera' : 'Scan QR with camera'}
    </button>
    {open && <video ref={video} muted playsInline aria-label="Ticket scanner camera" />}
    {error && <p role="alert">{error}</p>}
  </div>
}
