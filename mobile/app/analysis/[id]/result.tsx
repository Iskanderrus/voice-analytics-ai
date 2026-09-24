import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';

import type { AnalysisResult } from '../../../src/api/types';
import { api } from '../../../src/config';
import { BulletList, Button, Card, ErrorBox, Screen, styles } from '../../../src/ui';

const humanize = (key: string) => key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

export default function ResultScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);

  useEffect(() => {
    api
      .getResult(id)
      .then((response) => {
        if (response.state === 'completed') setResult(response.result);
        else router.replace(`/analysis/${id}`);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [id, router]);

  if (error) {
    return (
      <Screen>
        <ErrorBox message={error} />
        <Button label="Back" onPress={() => router.replace('/')} />
      </Screen>
    );
  }
  if (!result) {
    return (
      <Screen>
        <ActivityIndicator />
      </Screen>
    );
  }

  const standard = result.standard_analysis.structured_output;
  const custom = result.custom_analysis;

  return (
    <Screen>
      <Text style={styles.title}>Analysis result</Text>

      <Card title="Summary">
        <Text style={styles.body}>{standard.summary}</Text>
        <Text style={styles.muted}>
          Sentiment: {standard.sentiment} · Language: {standard.language ?? 'unknown'} · Speakers:{' '}
          {standard.speakers_count ?? 'unknown'}
        </Text>
      </Card>

      <Card title="Topics">
        <View style={styles.chipRow}>
          {standard.topics.map((topic) => (
            <Text key={topic} style={styles.chip}>
              {topic}
            </Text>
          ))}
        </View>
      </Card>

      <Card title="Key points">
        <BulletList items={standard.key_points} />
      </Card>

      <Card title="Action items">
        <BulletList
          items={standard.action_items.map((a) => (a.owner ? `${a.description} (${a.owner})` : a.description))}
        />
      </Card>

      {custom ? (
        <Card title={`${custom.template?.name ?? custom.analysis_type} · v${custom.template?.version}`}>
          <Text style={styles.body}>{custom.summary}</Text>
          {Object.entries(custom.structured_output)
            .filter(([key, value]) => key !== 'summary' && Array.isArray(value))
            .map(([key, value]) => (
              <View key={key} style={{ gap: 4 }}>
                <Text style={[styles.body, { fontWeight: '600' }]}>{humanize(key)}</Text>
                <BulletList items={value as string[]} />
              </View>
            ))}
        </Card>
      ) : (
        <Card title="Specialised analysis">
          <Text style={styles.muted}>No analysis template matched this recording.</Text>
        </Card>
      )}

      <Card title="Transcript">
        {showTranscript ? <Text style={styles.body}>{result.transcript.text}</Text> : null}
        <Button
          label={showTranscript ? 'Hide transcript' : 'Show transcript'}
          onPress={() => setShowTranscript(!showTranscript)}
          variant="secondary"
        />
      </Card>

      <Text style={styles.muted}>
        {result.transcript.provider}/{result.transcript.model} ·{' '}
        {result.standard_analysis.provenance.provider}/{result.standard_analysis.provenance.model} ·
        prompt {result.standard_analysis.provenance.prompt_version}
      </Text>
      <Button label="Analyse another file" onPress={() => router.replace('/')} />
    </Screen>
  );
}
