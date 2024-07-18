import type { CommonNodeType, ModelConfig, ValueSelector } from '@/app/components/workflow/types'
import type { RETRIEVE_TYPE } from '@/types/app'
import type {
  RerankingModeEnum,
  WeightedScoreEnum,
} from '@/models/datasets'

export type MultipleRetrievalConfig = {
  top_k: number
  score_threshold: number | null | undefined
  reranking_model?: {
    provider: string
    model: string
  }
  reranking_mode?: RerankingModeEnum
  weights?: {
    weight_type: WeightedScoreEnum
    vector_setting: {
      vector_weight: number
      embedding_provider_name: string
      embedding_model_name: string
    }
    keyword_setting: {
      keyword_weight: number
    }
  }
}

export type SingleRetrievalConfig = {
  model: ModelConfig
}

export type KnowledgeRetrievalNodeType = CommonNodeType & {
  query_variable_selector: ValueSelector
  dataset_ids: string[]
  retrieval_mode: RETRIEVE_TYPE
  multiple_retrieval_config?: MultipleRetrievalConfig
  single_retrieval_config?: SingleRetrievalConfig
}
