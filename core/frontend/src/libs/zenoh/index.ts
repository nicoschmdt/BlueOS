import {
  Config, QueryTarget, Receiver, RecvErr, Sample, Session, Subscriber,
} from '@eclipse-zenoh/zenoh-ts'

class ZenohManager {
  private static instance: ZenohManager

  private sessionPromise: Promise<Session> | null = null

  private constructor() {}

  public static getInstance(): ZenohManager {
    if (!ZenohManager.instance) {
      ZenohManager.instance = new ZenohManager()
    }
    return ZenohManager.instance
  }

  public static getWebsocketUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${protocol}://${window.location.host}/zenoh-api/`
  }

  public getSession(): Promise<Session> {
    if (!this.sessionPromise) {
      const url = ZenohManager.getWebsocketUrl()
      const config = new Config(url)
      this.sessionPromise = Session.open(config).catch((err) => {
        this.sessionPromise = null
        throw err
      })
    }
    return this.sessionPromise
  }

  public async query(
    key: string,
    target: QueryTarget,
    timeout = 30000,
    payload?: Uint8Array,
  ) : Promise<any | null> {
    const session = await this.getSession()
    if (!session) {
      console.error('Zenoh session not initialized')
      return null
    }

    const receiver: Receiver | void = session.get(key, {
      target,
      payload,
    })

    if (!(receiver instanceof Receiver)) {
      console.error('Failed to create query receiver. No queryable found or connection error.')
      return null
    }

    const timeoutPromise = new Promise<null>((resolve) => {
      setTimeout(() => resolve(null), timeout)
    })

    const replyPromise = receiver.receive()
    const reply = await Promise.race([replyPromise, timeoutPromise])

    if (reply === null || reply === RecvErr.Disconnected) {
      console.error('Query timeout: No response from zenoh queryable. '
        + 'The service may be unavailable or the extension may not exist.')
      return null
    }

    if (!reply || typeof (reply as any).result !== 'function') {
      console.error('Unexpected reply from zenoh queryable:', reply)
      return null
    }

    const sample = (reply as { result: () => Sample }).result()
    try {
      return JSON.parse(sample.payload().to_string())
    } catch (error) {
      console.error('Error parsing response:', error)
      return null
    }
  }

  public async* queryStream(
    key: string,
    target: QueryTarget,
    timeout = 120000,
    payload?: Uint8Array,
  ): AsyncGenerator<any, void, void> {
    const session = await this.getSession()
    if (!session) {
      console.error('Zenoh session not initialized')
      return
    }

    const receiver: Receiver | void = session.get(key, { target, payload })
    if (!(receiver instanceof Receiver)) {
      console.error('Failed to create query receiver. No queryable found or connection error.')
      return
    }

    const deadline = Date.now() + timeout
    while (true) {
      const remaining = deadline - Date.now()
      if (remaining <= 0) {
        console.error(`Query stream timed out after ${timeout}ms`)
        return
      }

      const timeoutPromise = new Promise<null>((resolve) => {
        setTimeout(() => resolve(null), remaining)
      })

      const reply = await Promise.race([receiver.receive(), timeoutPromise])
      if (reply === null || reply === RecvErr.Disconnected) return
      if (reply === RecvErr.MalformedReply) continue

      const sample = (reply as { result: () => Sample }).result()
      try {
        yield JSON.parse(sample.payload().to_string())
      } catch (error) {
        console.error('Error parsing stream reply:', error)
      }
    }
  }

  public async subscriber(topic: string, handler: (sample: Sample) => Promise<void>) : Promise<Subscriber | null> {
    const session = await this.getSession()
    if (!session) {
      console.error('Zenoh session not initialized')
      return null
    }

    return session.declare_subscriber(topic, {
      handler,
    })
  }
}

const zenoh = ZenohManager.getInstance()

export default zenoh
